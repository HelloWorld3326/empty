"""本地沙箱 —— 仅供开发调试，禁止上生产。

它把命令直接跑在网关进程所在的机器上。agent 一条 ``rm -rf`` 就能删掉网关自己，
一条 ``env`` 就能读到数据库密码。上生产必须用 :mod:`.k8s`。

之所以还留着它，是因为本地开发和跑评测集时不该依赖 k8s 集群。
构造时会打日志警告，且当 ``AGENTBASE_ENV=production`` 时直接拒绝启动。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .base import ExecResult, Sandbox, SandboxProvider

logger = logging.getLogger(__name__)


class LocalSandbox(Sandbox):
    def __init__(self, session_id: str, root: Path) -> None:
        self._session_id = session_id
        self._root = root
        for sub in ("uploads", "workspace", "outputs"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        self.last_used = time.time()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, path: str) -> Path:
        """把路径限制在沙箱根目录内，挡掉 ``../../etc/passwd``。"""
        if os.path.isabs(path):
            candidate = Path(path).resolve()
        else:
            candidate = (self._root / path.lstrip("/")).resolve()
        root = self._root.resolve()
        if root not in candidate.parents and candidate != root:
            raise PermissionError(f"路径越界: {path}（只能访问沙箱内的文件）")
        return candidate

    def exec(self, command: str, *, timeout: int = 120, cwd: str | None = None) -> ExecResult:
        self.last_used = time.time()
        workdir = self._resolve(cwd) if cwd else self._root / "workspace"
        try:
            proc = subprocess.run(  # noqa: S603 - 本地沙箱的本职就是执行任意命令
                ["/bin/bash", "-lc", command],
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,  # 退出码由调用方判断，非零不是异常
                # 不继承网关进程的环境变量——那里面有 DASHSCOPE_API_KEY 和库密码。
                env={
                    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                    "HOME": str(self._root),
                    "LANG": "C.UTF-8",
                },
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult(
                exit_code=124,
                stdout=_as_text(exc.stdout),
                stderr="命令超时",
                timed_out=True,
            )
        return ExecResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def read_file(self, path: str, *, max_bytes: int = 200_000) -> str:
        self.last_used = time.time()
        target = self._resolve(path)
        if not target.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        data = target.read_bytes()[:max_bytes]
        return data.decode("utf-8", "replace")

    def write_file(self, path: str, content: str) -> None:
        self.last_used = time.time()
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def list_outputs(self) -> list[str]:
        outputs = self._root / "outputs"
        return sorted(
            str(p.relative_to(self._root)) for p in outputs.rglob("*") if p.is_file()
        )

    def close(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)


class LocalSandboxProvider(SandboxProvider):
    def __init__(self, *, idle_timeout_seconds: int = 900, base_dir: str | None = None) -> None:
        if os.environ.get("AGENTBASE_ENV") == "production":
            raise RuntimeError(
                "本地沙箱不能用于生产环境：它把 agent 生成的代码直接跑在网关进程所在机器上。"
                "请把 config.yaml 的 sandbox.provider 改成 k8s。"
            )
        logger.warning("使用本地沙箱（仅限开发调试，无任何隔离）")
        self._base = Path(base_dir or tempfile.mkdtemp(prefix="agentbase-"))
        self._idle_timeout = idle_timeout_seconds
        self._boxes: dict[str, LocalSandbox] = {}

    def acquire(self, session_id: str) -> Sandbox:
        if session_id not in self._boxes:
            self._boxes[session_id] = LocalSandbox(session_id, self._base / session_id)
        return self._boxes[session_id]

    def release(self, session_id: str) -> None:
        box = self._boxes.pop(session_id, None)
        if box:
            box.close()

    def reap_idle(self) -> int:
        now = time.time()
        stale = [
            sid
            for sid, box in self._boxes.items()
            if now - box.last_used > self._idle_timeout
        ]
        for sid in stale:
            self.release(sid)
        return len(stale)


def _as_text(value: str | bytes | None) -> str:
    """超时异常里的 stdout 可能是 bytes 也可能是 str，取决于 Python 版本和参数。"""
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value
