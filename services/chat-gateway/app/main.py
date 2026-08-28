"""对话网关 —— 客户360 前端唯一对接的服务。

它负责四件事：
  1. 认出当前客户是谁（登录态 → customer_id）；
  2. 建会话，并把会话和客户绑定，让 MaxKB 工作流只能查到这个客户的数据；
  3. 转发对话并做流式透传，MaxKB 的 API Key 始终不出服务端；
  4. 限流、脱敏、审计、转人工。
"""
import json
import logging

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import audit, tools_client
from .auth import current_customer_id
from .config import settings
from .maxkb import MaxKBError, client
from .safety import limiter, sanitize_question
from .sessions import registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("chat-gateway")

app = FastAPI(title="客服 Agent 对话网关", version="1.0.0")

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.on_event("startup")
def _startup() -> None:
    settings.validate()
    if settings.auth_mode == "debug":
        logger.warning("AUTH_MODE=debug，仅可用于本地联调，上线前必须改成 jwt 或 introspect")
    logger.info("chat-gateway 启动，MaxKB 模式=%s", settings.maxkb_mode)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "maxkb_mode": settings.maxkb_mode, "auth_mode": settings.auth_mode}


# ------------------------------------------------------------------ 会话


class SessionResponse(BaseModel):
    conversation_id: str


@app.post("/api/chat/sessions", response_model=SessionResponse)
async def create_session(customer_id: str = Depends(current_customer_id)):
    limiter.check(f"session:{customer_id}")
    try:
        chat_id = await client.open_chat()
    except MaxKBError as exc:
        logger.error("打开 MaxKB 会话失败: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "客服服务暂不可用") from exc

    try:
        await tools_client.bind_session(chat_id, customer_id)
    except Exception as exc:  # 绑定不上就不能放行，否则会变成"能对话但查不到数据"
        logger.error("绑定会话失败: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "客服服务暂不可用") from exc

    registry.add(chat_id, customer_id)
    audit.record("session_created", customer_id=customer_id, chat_id=chat_id)
    return SessionResponse(conversation_id=chat_id)


# ------------------------------------------------------------------ 对话


class MessageRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    question: str = Field(min_length=1)


@app.post("/api/chat/messages")
async def send_message(req: MessageRequest, customer_id: str = Depends(current_customer_id)):
    """流式返回。前端用 EventSource 之外的方式读取（见 web/demo/index.html）。"""
    limiter.check(f"chat:{customer_id}")

    session = registry.get_for(req.conversation_id, customer_id)
    if session is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "会话不存在或已过期，请重新发起会话")

    question = sanitize_question(req.question)
    audit.record("question", customer_id=customer_id, chat_id=session.chat_id, question=question)

    variables = {}
    tool_token = tools_client.issue_tool_token(customer_id)
    if tool_token:
        variables["tool_token"] = tool_token

    async def event_stream():
        answer_chars = 0
        try:
            async for piece in client.stream_chat(session.chat_id, question, variables or None):
                answer_chars += len(piece)
                yield _sse({"type": "delta", "content": piece})
        except MaxKBError as exc:
            logger.error("对话失败: %s", exc)
            yield _sse({"type": "error", "message": "回答生成失败，请稍后重试或转人工"})
        finally:
            audit.record(
                "answer", customer_id=customer_id, chat_id=session.chat_id, answer_chars=answer_chars
            )
            yield _sse({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ------------------------------------------------------------------ 转人工


class HandoffRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    title: str = Field(default="客户要求人工客服", max_length=100)
    detail: str = Field(default="", max_length=2000)


@app.post("/api/chat/handoff")
async def handoff(req: HandoffRequest, customer_id: str = Depends(current_customer_id)):
    session = registry.get_for(req.conversation_id, customer_id)
    if session is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "会话不存在或已过期")
    try:
        ticket = await tools_client.create_ticket(session.chat_id, req.title, req.detail)
    except Exception as exc:
        logger.error("建单失败: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "转人工失败，请稍后重试") from exc
    audit.record("handoff", customer_id=customer_id, chat_id=session.chat_id, ticket=ticket)
    return ticket
