"""Gateway API。

只有五组接口，够前端跑通完整链路：

    POST /api/threads                        新建会话
    POST /api/threads/{id}/runs              提问，SSE 流式返回
    POST /api/threads/{id}/resume            回答 SQL 确认（批准/拒绝）后继续
    GET  /api/threads/{id}/outputs           列出产物文件
    POST /api/skills/reload                  改完 SKILL.md 后热加载
    POST /api/schema/refresh                 库结构变更后刷新缓存

鉴权：MVP 用一个共享 token（``AGENTBASE_API_TOKEN``），并从请求头取
``X-Agentbase-Role`` 决定数据权限。接公司 SSO 时替换 ``resolve_principal``
这一个函数即可。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command
from pydantic import BaseModel

from ..runtime import Runtime

logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    message: str
    role: str | None = None


class ResumeRequest(BaseModel):
    approved: bool
    comment: str | None = None
    role: str | None = None


class Principal(BaseModel):
    user_id: str
    role: str | None = None


def resolve_principal(
    authorization: str | None = Header(default=None),
    x_agentbase_role: str | None = Header(default=None),
    x_agentbase_user: str | None = Header(default=None),
) -> Principal:
    """MVP 版鉴权。接 SSO 时只需要替换这个函数。"""
    expected = os.environ.get("AGENTBASE_API_TOKEN")
    if expected and authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="未授权")
    return Principal(user_id=x_agentbase_user or "anonymous", role=x_agentbase_role)


def create_app(runtime: Runtime | None = None) -> FastAPI:
    rt = runtime or Runtime()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt.start()
        yield
        rt.shutdown()

    app = FastAPI(title="AgentBase Gateway", version="0.1.0", lifespan=lifespan)
    app.state.runtime = rt

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "skills": len(rt.skills.all()),
            "skill_errors": rt.skills.errors,
            "datasources": rt.datasources.names(),
            "tables_cached": len(rt.schema_cache.tables()),
        }

    @app.post("/api/threads")
    def create_thread(principal: Principal = Depends(resolve_principal)) -> dict[str, str]:
        return {"thread_id": f"t-{uuid.uuid4().hex[:16]}", "user_id": principal.user_id}

    @app.post("/api/threads/{thread_id}/runs")
    async def run(
        thread_id: str, req: RunRequest, principal: Principal = Depends(resolve_principal)
    ) -> StreamingResponse:
        role = req.role or principal.role
        return StreamingResponse(
            _stream(rt, thread_id, role, {"messages": [HumanMessage(content=req.message)]}),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/threads/{thread_id}/resume")
    async def resume(
        thread_id: str, req: ResumeRequest, principal: Principal = Depends(resolve_principal)
    ) -> StreamingResponse:
        """回答 SQL 确认。拒绝时把用户的理由一起带回去，模型才知道该怎么改。"""
        payload = {"approved": req.approved, "comment": req.comment}
        return StreamingResponse(
            _stream(rt, thread_id, req.role or principal.role, Command(resume=payload)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/threads/{thread_id}/outputs")
    def outputs(thread_id: str, _: Principal = Depends(resolve_principal)) -> dict[str, Any]:
        sandbox = rt.sandboxes.acquire(thread_id)
        return {"files": sandbox.list_outputs()}

    @app.delete("/api/threads/{thread_id}")
    def close_thread(thread_id: str, _: Principal = Depends(resolve_principal)) -> dict[str, str]:
        rt.end_session(thread_id)
        return {"status": "closed"}

    @app.post("/api/skills/reload")
    def reload_skills(_: Principal = Depends(resolve_principal)) -> dict[str, Any]:
        count, errors = rt.reload_skills()
        return {"loaded": count, "errors": errors}

    @app.post("/api/schema/refresh")
    def refresh_schema(_: Principal = Depends(resolve_principal)) -> dict[str, Any]:
        return {"tables": rt.refresh_schema()}

    return app


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream(
    rt: Runtime, thread_id: str, role: str | None, payload: Any
) -> AsyncIterator[str]:
    """把 LangGraph 的事件流翻译成 SSE。

    前端需要区分四类事件：文本增量、工具调用开始、工具结果、以及
    需要用户确认的中断。中断这一类是问数场景的核心交互，不能只当报错处理。
    """
    try:
        ctx = rt.make_context(thread_id, role)
    except KeyError as exc:
        yield _sse("error", {"message": str(exc)})
        return

    graph = rt.compile_graph(ctx)
    config = {"configurable": {"thread_id": thread_id}}

    try:
        async for mode, chunk in graph.astream(
            payload, config=config, stream_mode=["messages", "updates"]
        ):
            if mode == "messages":
                message, _meta = chunk
                if isinstance(message, AIMessage) and message.content:
                    text = (
                        message.content
                        if isinstance(message.content, str)
                        else str(message.content)
                    )
                    yield _sse("token", {"text": text})
                elif isinstance(message, ToolMessage):
                    yield _sse(
                        "tool_result",
                        {"tool": message.name, "content": _clip(str(message.content))},
                    )
            elif mode == "updates":
                for node, update in (chunk or {}).items():
                    if node == "__interrupt__":
                        for item in update:
                            yield _sse("interrupt", _interrupt_payload(item))
                        continue
                    for msg in (update or {}).get("messages", []) or []:
                        for call in getattr(msg, "tool_calls", None) or []:
                            yield _sse(
                                "tool_call",
                                {"tool": call.get("name"), "args": call.get("args")},
                            )
        yield _sse("done", {"outputs": ctx.sandbox.list_outputs(), "sql": ctx.executed_sql})
    except Exception as exc:  # pragma: no cover - 防止流中断时前端一直挂着
        logger.exception("运行失败 thread=%s", thread_id)
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})


def _interrupt_payload(item: Any) -> dict[str, Any]:
    value = getattr(item, "value", item)
    return value if isinstance(value, dict) else {"value": str(value)}


def _clip(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + f"...（截断，共 {len(text)} 字符）"
