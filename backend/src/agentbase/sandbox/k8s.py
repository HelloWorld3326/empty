"""K8s 沙箱：一个会话一个 Pod。

生产用的实现。相比本地沙箱多做四件事：

1. **隔离**：每个会话独立 Pod，非 root 运行，只读根文件系统，
   工作区挂 ``emptyDir``，drop 掉全部 capability。
2. **断网**：靠 NetworkPolicy（见 deploy/networkpolicy.yaml）默认拒绝所有出网，
   只放行 ``egress_allowlist``。**这条不是可选项**——沙箱里跑的是模型生成的代码，
   而模型的输入包含数据库里的任意内容，提示注入的出口就在这里。
3. **配额**：CPU/内存 limit，防单会话打满节点。
4. **回收**：空闲超时删 Pod。

前置条件：网关 ServiceAccount 需要在目标 namespace 有 pods 的
``create/get/delete`` 和 ``pods/exec`` 的 ``create`` 权限（见 deploy/rbac.yaml）。
这个审批在多数公司要走一两周，建议立项当天就提。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..config import SandboxConfig
from .base import ExecResult, Sandbox, SandboxProvider

logger = logging.getLogger(__name__)

_POD_READY_TIMEOUT = 120


def _require_client() -> Any:
    try:
        from kubernetes import client
        from kubernetes import config as kube_config
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError(
            "缺少 kubernetes 客户端，请安装可选依赖: pip install -e '.[k8s]'"
        ) from exc
    try:
        kube_config.load_incluster_config()
    except Exception:
        kube_config.load_kube_config()
    return client


class K8sSandbox(Sandbox):
    def __init__(self, session_id: str, pod_name: str, cfg: SandboxConfig, client: Any) -> None:
        self._session_id = session_id
        self._pod = pod_name
        self._cfg = cfg
        self._client = client
        self._core = client.CoreV1Api()
        self.last_used = time.time()

    @property
    def session_id(self) -> str:
        return self._session_id

    def _exec_raw(self, argv: list[str], *, timeout: int) -> ExecResult:
        from kubernetes.stream import stream

        self.last_used = time.time()
        resp = stream(
            self._core.connect_get_namespaced_pod_exec,
            self._pod,
            self._cfg.namespace,
            command=argv,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        stdout, stderr = [], []
        deadline = time.time() + timeout
        while resp.is_open():
            if time.time() > deadline:
                resp.close()
                return ExecResult(124, "".join(stdout), "命令超时", timed_out=True)
            resp.update(timeout=1)
            if resp.peek_stdout():
                stdout.append(resp.read_stdout())
            if resp.peek_stderr():
                stderr.append(resp.read_stderr())
        err = resp.read_channel(3) if hasattr(resp, "read_channel") else ""
        resp.close()
        return ExecResult(
            exit_code=_exit_code_from_status(err),
            stdout="".join(stdout),
            stderr="".join(stderr),
        )

    def exec(self, command: str, *, timeout: int = 120, cwd: str | None = None) -> ExecResult:
        workdir = cwd or f"{self._cfg.workspace_root}/workspace"
        wrapped = f"cd {_shell_quote(workdir)} && {command}"
        return self._exec_raw(["/bin/bash", "-lc", wrapped], timeout=timeout)

    def read_file(self, path: str, *, max_bytes: int = 200_000) -> str:
        full = self._abs(path)
        result = self._exec_raw(
            ["/bin/bash", "-lc", f"head -c {max_bytes} {_shell_quote(full)}"],
            timeout=30,
        )
        if result.exit_code != 0:
            raise FileNotFoundError(f"读取失败: {path} — {result.stderr.strip()}")
        return result.stdout

    def write_file(self, path: str, content: str) -> None:
        import base64

        full = self._abs(path)
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        # 走 base64 而不是直接拼字符串，避免内容里的引号/换行破坏命令。
        quoted = _shell_quote(full)
        script = f"mkdir -p $(dirname {quoted}) && echo {encoded} | base64 -d > {quoted}"
        result = self._exec_raw(["/bin/bash", "-lc", script], timeout=60)
        if result.exit_code != 0:
            raise OSError(f"写入失败: {path} — {result.stderr.strip()}")

    def list_outputs(self) -> list[str]:
        outputs = f"{self._cfg.workspace_root}/outputs"
        result = self._exec_raw(
            ["/bin/bash", "-lc", f"find {_shell_quote(outputs)} -type f -printf '%P\\n' 2>/dev/null"],  # noqa: E501
            timeout=30,
        )
        return [line for line in result.stdout.splitlines() if line.strip()]

    def _abs(self, path: str) -> str:
        if path.startswith("/"):
            if not path.startswith(self._cfg.workspace_root):
                raise PermissionError(f"路径越界: {path}")
            return path
        return f"{self._cfg.workspace_root}/workspace/{path}"

    def close(self) -> None:
        try:
            self._core.delete_namespaced_pod(
                self._pod, self._cfg.namespace, grace_period_seconds=5
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("删除沙箱 Pod %s 失败: %s", self._pod, exc)


class K8sSandboxProvider(SandboxProvider):
    def __init__(self, cfg: SandboxConfig) -> None:
        self._cfg = cfg
        self._client = _require_client()
        self._core = self._client.CoreV1Api()
        self._boxes: dict[str, K8sSandbox] = {}

    def acquire(self, session_id: str) -> Sandbox:
        if session_id in self._boxes:
            return self._boxes[session_id]
        pod_name = f"sandbox-{session_id[:20].lower()}"
        self._core.create_namespaced_pod(self._cfg.namespace, self._pod_spec(pod_name, session_id))
        self._wait_ready(pod_name)
        box = K8sSandbox(session_id, pod_name, self._cfg, self._client)
        self._boxes[session_id] = box
        return box

    def _pod_spec(self, pod_name: str, session_id: str) -> dict[str, Any]:
        c = self._cfg
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "labels": {
                    "app": "agentbase-sandbox",
                    "agentbase/session": session_id[:63],
                    # NetworkPolicy 靠这个标签选中沙箱 Pod 做默认拒绝出网。
                    "agentbase/egress": "restricted",
                },
            },
            "spec": {
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,  # 别把 SA token 送进沙箱
                "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "fsGroup": 10001},
                "containers": [
                    {
                        "name": "sandbox",
                        "image": c.image,
                        "command": ["/bin/bash", "-c", "sleep infinity"],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "resources": {
                            "limits": {"cpu": c.cpu_limit, "memory": c.memory_limit},
                            "requests": {"cpu": "100m", "memory": "256Mi"},
                        },
                        "volumeMounts": [
                            {"name": "workspace", "mountPath": c.workspace_root},
                            {"name": "tmp", "mountPath": "/tmp"},  # noqa: S108 - 容器内挂载点，非宿主机临时文件
                        ],
                    }
                ],
                "volumes": [
                    {"name": "workspace", "emptyDir": {"sizeLimit": "2Gi"}},
                    {"name": "tmp", "emptyDir": {"sizeLimit": "512Mi"}},
                ],
            },
        }

    def _wait_ready(self, pod_name: str) -> None:
        deadline = time.time() + _POD_READY_TIMEOUT
        while time.time() < deadline:
            pod = self._core.read_namespaced_pod(pod_name, self._cfg.namespace)
            if pod.status and pod.status.phase == "Running":
                return
            if pod.status and pod.status.phase in ("Failed", "Succeeded"):
                raise RuntimeError(f"沙箱 Pod {pod_name} 启动失败: {pod.status.phase}")
            time.sleep(1)
        raise TimeoutError(f"沙箱 Pod {pod_name} 在 {_POD_READY_TIMEOUT}s 内未就绪")

    def release(self, session_id: str) -> None:
        box = self._boxes.pop(session_id, None)
        if box:
            box.close()

    def reap_idle(self) -> int:
        now = time.time()
        stale = [
            sid for sid, box in self._boxes.items()
            if now - box.last_used > self._cfg.idle_timeout_seconds
        ]
        for sid in stale:
            self.release(sid)
        return len(stale)


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _exit_code_from_status(raw: str) -> int:
    """k8s exec 的退出码藏在 channel 3 的 JSON 状态里。"""
    if not raw:
        return 0
    try:
        import json

        status = json.loads(raw)
    except Exception:
        return 0
    if status.get("status") == "Success":
        return 0
    for cause in status.get("details", {}).get("causes", []):
        if cause.get("reason") == "ExitCode":
            try:
                return int(cause.get("message", 1))
            except ValueError:
                return 1
    return 1


def build_provider(cfg: SandboxConfig) -> SandboxProvider:
    if cfg.provider == "k8s":
        return K8sSandboxProvider(cfg)
    from .local import LocalSandboxProvider

    return LocalSandboxProvider(idle_timeout_seconds=cfg.idle_timeout_seconds)
