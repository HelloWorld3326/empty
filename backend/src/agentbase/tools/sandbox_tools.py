"""沙箱工具：bash 和文件读写。

沙箱里没有任何凭证，也不能出网（生产环境由 NetworkPolicy 保证），
所以这里给模型的权限可以放得比较开——它能做的最坏的事就是把自己的
工作区搞乱，而工作区是一次性的。
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .context import RunContext


class BashInput(BaseModel):
    command: str = Field(description="要执行的 bash 命令")
    timeout: int = Field(default=120, description="超时秒数，上限 600")


class ReadFileInput(BaseModel):
    path: str = Field(description="沙箱内路径，相对路径以 workspace 为根")


class WriteFileInput(BaseModel):
    path: str = Field(description="沙箱内路径，相对路径以 workspace 为根")
    content: str = Field(description="文件完整内容")


def build_sandbox_tools(ctx: RunContext) -> list[StructuredTool]:
    def bash(command: str, timeout: int = 120) -> str:
        return ctx.sandbox.exec(command, timeout=min(timeout, 600)).to_model_text()

    def read_file(path: str) -> str:
        try:
            return ctx.sandbox.read_file(path)
        except (FileNotFoundError, PermissionError) as exc:
            return f"错误: {exc}"

    def write_file(path: str, content: str) -> str:
        try:
            ctx.sandbox.write_file(path, content)
        except (OSError, PermissionError) as exc:
            return f"错误: {exc}"
        return f"已写入 {path}（{len(content)} 字符）"

    return [
        StructuredTool.from_function(
            func=bash,
            name="bash",
            description=(
                "在沙箱里执行 bash 命令。已预装 python3/pandas/matplotlib。"
                "用它做数据加工、画图、生成报表文件。"
                "要交付给用户的产物请写到 outputs/ 目录下。"
            ),
            args_schema=BashInput,
        ),
        StructuredTool.from_function(
            func=read_file,
            name="read_file",
            description="读取沙箱内的文件内容。",
            args_schema=ReadFileInput,
        ),
        StructuredTool.from_function(
            func=write_file,
            name="write_file",
            description="把内容写入沙箱文件。写脚本、存中间结果时用它。",
            args_schema=WriteFileInput,
        ),
    ]
