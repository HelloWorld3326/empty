"""工具装配。

工具集刻意保持在个位数。每多一个工具，模型选错的概率就上升一截，
而模型选错工具的代价（一次无意义的 loop）比工具本身的价值高得多。
需要新能力优先考虑写 skill 教它用 bash，而不是加工具。
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from .context import RunContext
from .data_tools import build_data_tools
from .sandbox_tools import build_sandbox_tools
from .skill_tools import build_skill_tools


def build_tools(ctx: RunContext) -> list[BaseTool]:
    tools: list[BaseTool] = []
    tools.extend(build_skill_tools(ctx))
    tools.extend(build_data_tools(ctx))
    tools.extend(build_sandbox_tools(ctx))
    return _filter_by_skill_policy(tools, ctx)


def _filter_by_skill_policy(tools: list[BaseTool], ctx: RunContext) -> list[BaseTool]:
    """skill 可以用 frontmatter 的 allowed_tools 收窄工具集。

    MVP 只在显式 /skill 触发时生效（由调用方预先设置），这里保留钩子。
    """
    return tools
