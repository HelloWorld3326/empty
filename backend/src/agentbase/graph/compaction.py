"""上下文压缩。

agent loop 每一轮都把完整对话重发给模型，token 消耗随轮次近似平方级增长。
一个跑 20 轮的任务，不压缩的话最后几轮每轮都在重发前面所有的 SQL 结果和
bash 输出。所以压缩不是优化项，是能不能跑长任务的前提。

策略刻意保守：保留 system、保留最近 N 条消息、把中间部分交给模型摘要。
更激进的策略（比如按工具类型丢弃）容易丢掉关键中间结论，
在没有评测集之前不要上。
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = """请把下面这段 agent 工作记录压缩成一段简报，供后续继续工作时参考。

必须保留：
- 用户的原始目标和中途提出的修改
- 已经查明的事实（表结构、口径、关键数字），带上出处
- 执行过的 SQL 的要点和结论
- 走过的弯路和已排除的可能（避免后续重复尝试）
- 尚未完成的部分

可以丢弃：完整的查询结果明细、bash 的冗长输出、重复的探查过程。

工作记录：
{transcript}
"""


def estimate_tokens(messages: list[BaseMessage]) -> int:
    """粗估 token 数。

    中文约 1 字 1 token，英文约 4 字符 1 token，这里按中英混合取 2 字符/token。
    只用来决定要不要触发压缩，不需要精确——精确计数要调 tokenizer，
    在热路径上不值得。
    """
    total = 0
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        total += len(content) // 2
        for call in getattr(msg, "tool_calls", None) or []:
            total += len(str(call)) // 2
    return total


def _render(messages: list[BaseMessage]) -> str:
    lines = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            role = "用户"
        elif isinstance(msg, AIMessage):
            role = "助手"
        elif isinstance(msg, ToolMessage):
            role = f"工具结果({msg.name})"
        else:
            role = "系统"
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        lines.append(f"[{role}] {content[:2000]}")
        for call in getattr(msg, "tool_calls", None) or []:
            lines.append(f"[助手调用工具] {call.get('name')}({call.get('args')})")
    return "\n".join(lines)


def compact(
    messages: list[BaseMessage], model: BaseChatModel, *, keep_recent: int
) -> list[BaseMessage] | None:
    """压缩消息列表。无需压缩或压缩失败时返回 None，调用方继续用原列表。"""
    system = [m for m in messages if isinstance(m, SystemMessage)]
    rest = [m for m in messages if not isinstance(m, SystemMessage)]

    if len(rest) <= keep_recent + 2:
        return None

    head, tail = rest[:-keep_recent], rest[-keep_recent:]

    # 不能从 ToolMessage 开头——多数厂商的接口要求 tool 消息必须紧跟在
    # 带 tool_calls 的助手消息之后，否则报 400。
    while tail and isinstance(tail[0], ToolMessage):
        head.append(tail.pop(0))
    if not tail:
        return None

    try:
        prompt = _SUMMARY_PROMPT.format(transcript=_render(head))
        summary = model.invoke([HumanMessage(content=prompt)])
    except Exception as exc:
        # 压缩失败不该让整个任务挂掉，退回原上下文继续跑。
        logger.warning("上下文压缩失败，保持原上下文: %s", exc)
        return None

    text = summary.content if isinstance(summary.content, str) else str(summary.content)
    marker = HumanMessage(content=f"【以下是此前工作的压缩摘要】\n{text}")
    logger.info("上下文压缩: %d 条 -> %d 条", len(messages), len(system) + 1 + len(tail))
    return [*system, marker, *tail]
