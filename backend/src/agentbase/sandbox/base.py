"""沙箱抽象。

沙箱负责跑 agent 生成的任意代码，所以它的安全模型只有一条假设：
**沙箱里的一切都可能泄露，也可能被破坏。**

由此推出两条硬约束，实现方必须遵守：

1. 不往沙箱里注入任何凭证。数据库连接在网关侧完成（见 datasources/registry.py），
   沙箱只拿得到查询结果文件。
2. 出网默认全禁，按 ``egress_allowlist`` 放行。

目录约定（对齐 DeerFlow，方便沿用它的 skill 写法）::

    /mnt/user-data/uploads    用户上传的输入文件，只读
    /mnt/user-data/workspace  agent 的工作区，可读写
    /mnt/user-data/outputs    产物区，前端从这里列文件给用户下载
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    def to_model_text(self, max_chars: int = 8000) -> str:
        parts = []
        if self.stdout:
            parts.append(f"stdout:\n{_truncate(self.stdout, max_chars)}")
        if self.stderr:
            parts.append(f"stderr:\n{_truncate(self.stderr, max_chars)}")
        if self.timed_out:
            parts.append("⚠ 命令超时被终止")
        if not parts:
            parts.append("(命令无输出)")
        parts.append(f"exit_code={self.exit_code}")
        return "\n".join(parts)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - limit
    return f"{text[:half]}\n...（省略 {omitted} 字符）...\n{text[-half:]}"


class Sandbox(ABC):
    """一个会话对应一个沙箱实例。"""

    @property
    @abstractmethod
    def session_id(self) -> str: ...

    @abstractmethod
    def exec(self, command: str, *, timeout: int = 120, cwd: str | None = None) -> ExecResult: ...

    @abstractmethod
    def read_file(self, path: str, *, max_bytes: int = 200_000) -> str: ...

    @abstractmethod
    def write_file(self, path: str, content: str) -> None: ...

    @abstractmethod
    def list_outputs(self) -> list[str]: ...

    @abstractmethod
    def close(self) -> None: ...


class SandboxProvider(ABC):
    """按会话创建/回收沙箱。"""

    @abstractmethod
    def acquire(self, session_id: str) -> Sandbox: ...

    @abstractmethod
    def release(self, session_id: str) -> None: ...

    def reap_idle(self) -> int:
        """回收空闲沙箱，返回回收数量。默认不做。"""
        return 0
