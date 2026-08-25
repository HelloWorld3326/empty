"""Skill 工具 —— 渐进式加载的执行端。

system prompt 里只有 skill 的名字和一句话描述，正文靠 ``read_skill`` 按需拉取。
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .context import RunContext


class ReadSkillInput(BaseModel):
    name: str = Field(description="skill 名称，取自可用 skill 列表")


def build_skill_tools(ctx: RunContext) -> list[StructuredTool]:
    def read_skill(name: str) -> str:
        skill = ctx.skills.get(name)
        if skill is None:
            available = ", ".join(s.name for s in ctx.skills.all()) or "(无)"
            return f"没有名为 `{name}` 的 skill。可用: {available}"
        extras = sorted(
            p.name for p in skill.directory.glob("*") if p.is_file() and p.name != "SKILL.md"
        )
        text = skill.body
        if extras:
            # 附属资料只列文件名，同样是按需读取，不一次性灌进上下文。
            text += (
                f"\n\n---\n本 skill 目录下还有这些资料文件，需要时用 bash 读取"
                f"（目录 {skill.directory}）: {', '.join(extras)}"
            )
        return text

    return [
        StructuredTool.from_function(
            func=read_skill,
            name="read_skill",
            description=(
                "读取某个 skill 的完整说明。当用户的问题命中了可用 skill 列表里的某一项时，"
                "先读它再动手——里面写着这类任务的正确做法、业务口径和踩过的坑。"
            ),
            args_schema=ReadSkillInput,
        )
    ]
