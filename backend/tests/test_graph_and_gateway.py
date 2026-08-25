"""图与网关的装配冒烟测试。

不打真实模型接口，只验证「东西能拼起来、路由都在」——这类错误如果留到
启动时才发现，调试成本比在这里挡住高一个量级。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agentbase.graph.agent import build_agent_graph, build_checkpointer
from agentbase.graph.compaction import compact, estimate_tokens
from agentbase.graph.prompt import build_system_prompt


def test_graph_compiles_with_checkpointer(run_context, config):
    config.llm.api_key = "sk-test-not-used"
    saver, close = build_checkpointer(config)
    try:
        graph = build_agent_graph(run_context, config).compile(checkpointer=saver)
        assert {"agent", "tools"} <= set(graph.get_graph().nodes)
    finally:
        close()


def test_build_chat_model_fails_loudly_without_key(run_context, config):
    config.llm.api_key = ""
    with pytest.raises(RuntimeError, match="api_key"):
        build_agent_graph(run_context, config)


def test_system_prompt_lists_skills_and_datasources(run_context, config):
    prompt = build_system_prompt(
        config, run_context.skills, run_context.datasources, run_context.role
    )
    assert "指标口径" in prompt          # skill 目录
    assert "shop" in prompt              # 数据源
    assert "search_tables" in prompt     # 强制的探查顺序
    # 渐进式加载：skill 正文不能出现在 system prompt 里
    assert "orders.created_at 是 UTC" not in prompt


def test_compaction_keeps_system_and_recent_messages():
    class FakeSummarizer:
        def invoke(self, messages):
            return AIMessage(content="摘要内容")

    messages = [SystemMessage(content="系统")]
    for i in range(20):
        messages.append(HumanMessage(content=f"问题{i}"))
        messages.append(AIMessage(content=f"回答{i}"))

    out = compact(messages, FakeSummarizer(), keep_recent=4)
    assert out is not None
    assert isinstance(out[0], SystemMessage)
    assert "摘要内容" in out[1].content
    assert out[-1].content == "回答19"
    assert len(out) < len(messages)


def test_compaction_never_starts_tail_with_tool_message():
    """多数厂商要求 tool 消息必须紧跟带 tool_calls 的助手消息，否则报 400。"""

    class FakeSummarizer:
        def invoke(self, messages):
            return AIMessage(content="摘要")

    messages = [SystemMessage(content="系统")]
    for i in range(10):
        messages.append(HumanMessage(content=f"问题{i}"))
        messages.append(
            AIMessage(content="", tool_calls=[{"name": "bash", "args": {}, "id": f"c{i}"}])
        )
        messages.append(ToolMessage(content=f"结果{i}", tool_call_id=f"c{i}", name="bash"))

    out = compact(messages, FakeSummarizer(), keep_recent=3)
    assert out is not None
    assert not isinstance(out[2], ToolMessage)


def test_compaction_survives_summarizer_failure():
    """压缩失败不能让整个任务挂掉，退回原上下文继续跑。"""

    class BrokenSummarizer:
        def invoke(self, messages):
            raise RuntimeError("模型超时")

    messages = [SystemMessage(content="系统")] + [HumanMessage(content=f"q{i}") for i in range(20)]
    assert compact(messages, BrokenSummarizer(), keep_recent=4) is None


def test_estimate_tokens_grows_with_content():
    short = [HumanMessage(content="短")]
    long = [HumanMessage(content="长" * 1000)]
    assert estimate_tokens(long) > estimate_tokens(short)


def test_gateway_health_and_routes(config, monkeypatch, tmp_path):
    from agentbase.gateway.app import create_app
    from agentbase.runtime import Runtime

    config.llm.api_key = "sk-test-not-used"
    rt = Runtime(config)
    rt.schema_cache.path = tmp_path / "schema.json"
    app = create_app(rt)

    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert "shop" in health["datasources"]
        assert health["skills"] >= 1

        thread = client.post("/api/threads").json()
        assert thread["thread_id"].startswith("t-")

        assert client.post("/api/skills/reload").json()["loaded"] >= 1
        assert client.get(f"/api/threads/{thread['thread_id']}/outputs").json() == {"files": []}


def test_gateway_rejects_bad_token(config, monkeypatch, tmp_path):
    from agentbase.gateway.app import create_app
    from agentbase.runtime import Runtime

    monkeypatch.setenv("AGENTBASE_API_TOKEN", "secret")
    config.llm.api_key = "sk-test-not-used"
    rt = Runtime(config)
    rt.schema_cache.path = tmp_path / "schema.json"

    with TestClient(create_app(rt)) as client:
        assert client.post("/api/threads").status_code == 401
        ok = client.post("/api/threads", headers={"Authorization": "Bearer secret"})
        assert ok.status_code == 200


def test_sqlite_checkpointer_is_persistent_not_memory(config):
    """确认没有静默退化成内存 checkpointer。

    退化了平台照样启动、对话照样能聊，只是重启丢状态、SQL 确认挂起后恢复不了——
    这种故障很难在开发阶段发现，所以在这里钉死。
    """
    from langgraph.checkpoint.memory import InMemorySaver

    saver, close = build_checkpointer(config)
    try:
        assert config.checkpoint_dsn.startswith("sqlite")
        assert not isinstance(saver, InMemorySaver), (
            "checkpointer 退化成了内存实现，请安装 langgraph-checkpoint-sqlite"
        )
    finally:
        close()
