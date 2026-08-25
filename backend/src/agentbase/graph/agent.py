"""Agent 主循环。

结构就是最朴素的 ReAct：模型 → 工具 → 模型，直到模型不再要求调工具。

刻意没做的事：
- 没有 planner/reporter 之类的固定角色分工。固定流程在开放任务上是负担，
  而且会掩盖模型本身的能力问题。
- 没有 sub-agent。图已经写成可以递归调用自己的形状（见 ``build_agent_graph``
  的入参），二期加 ``task`` 工具即可，不用改结构。

有兜底的地方：迭代上限、上下文压缩、工具异常不中断整个任务。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from ..config import Config
from ..llm import bind_tools, build_chat_model
from ..tools.context import RunContext
from ..tools.registry import build_tools
from .compaction import compact, estimate_tokens
from .prompt import build_system_prompt
from .state import AgentState

logger = logging.getLogger(__name__)


def build_agent_graph(ctx: RunContext, config: Config | None = None) -> Any:
    config = config or ctx.config
    tools = build_tools(ctx)
    model = build_chat_model(config.llm)
    model_with_tools = bind_tools(model, tools, config.llm)
    # 压缩用一个不带工具、非流式的模型实例，避免它误触发工具调用。
    summarizer = build_chat_model(config.llm, streaming=False)

    system_prompt = build_system_prompt(config, ctx.skills, ctx.datasources, ctx.role)

    def agent_node(state: AgentState) -> dict[str, Any]:
        messages = list(state["messages"])
        iterations = state.get("iterations", 0)

        if iterations >= config.agent.max_iterations:
            return {
                "messages": [
                    AIMessage(
                        content=(
                            f"已达到单次任务的最大步数上限（{config.agent.max_iterations} 步）"
                            "，我先停在这里。请看看上面已经完成的部分，"
                            "如果需要继续，可以把任务拆小一点再让我做。"
                        )
                    )
                ],
                "iterations": iterations,
            }

        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt), *messages]

        if estimate_tokens(messages) > config.agent.compaction_trigger_tokens:
            compacted = compact(
                messages, summarizer, keep_recent=config.agent.compaction_keep_recent_messages
            )
            if compacted:
                messages = compacted

        response = model_with_tools.invoke(messages)
        return {"messages": [response], "iterations": iterations + 1}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    # handle_tool_errors: 工具报错时把错误文本回给模型让它自己纠正，
    # 而不是让整个任务崩掉。模型改 SQL 的成功率比人想象的高。
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph


def build_checkpointer(config: Config) -> tuple[Any, Callable[[], None]]:
    """会话状态持久化。返回 ``(saver, close)``。

    有 checkpointer 才能做三件事：中断后恢复、SQL 人工确认（图挂起等待用户点确认）、
    以及从某一步分叉重跑。这三件都不是可选功能，所以 checkpointer 是必需的。

    langgraph 的 Sqlite/Postgres saver 是通过上下文管理器创建的，直接把它交给
    ``compile(checkpointer=...)`` 会报类型错误。这里统一在内部完成 enter，
    对外只暴露一个可用的 saver 和对应的关闭函数，免得每个调用方各踩一遍。
    """
    dsn = config.checkpoint_dsn

    def _enter(cm: Any) -> tuple[Any, Callable[[], None]]:
        saver = cm.__enter__()
        if hasattr(saver, "setup"):
            saver.setup()
        return saver, lambda: cm.__exit__(None, None, None)

    if dsn.startswith("postgres"):
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            return _enter(PostgresSaver.from_conn_string(dsn))
        except ImportError:
            logger.warning(
                "未安装 langgraph-checkpoint-postgres，回退到内存 checkpointer："
                "会话状态重启即丢。生产环境请安装: pip install -e '.[postgres]'"
            )
    elif dsn.startswith("sqlite"):
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver

            return _enter(SqliteSaver.from_conn_string(dsn.replace("sqlite:///", "")))
        except ImportError:
            logger.warning("未安装 langgraph-checkpoint-sqlite，回退到内存 checkpointer")

    from langgraph.checkpoint.memory import InMemorySaver

    # 内存 checkpointer 重启即丢，只能用于本地调试。
    return InMemorySaver(), lambda: None
