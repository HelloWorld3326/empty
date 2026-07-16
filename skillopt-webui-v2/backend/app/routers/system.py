"""系统信息与后端连通性预检。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import skillopt_compat as compat
from ..auth import AuthDep
from ..errors import ok

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
async def health(request: Request):
    settings = request.app.state.settings
    repo_ok = (settings.repo_root / "scripts" / "train.py").is_file()
    return ok({
        "status": "up",
        "repo_root": str(settings.repo_root),
        "repo_ok": repo_ok,
        "max_concurrent_jobs": settings.max_concurrent_jobs,
        "auth_mode": settings.auth_mode,
    })


@router.get("/backends")
async def backends(request: Request, config: str = "", _auth: dict = AuthDep):
    """对指定预设（或全部预设）执行启动前预检，返回每项的可读结论。

    前端在"启动训练"按钮前调用，把 error 直接展示给用户。
    """
    settings = request.app.state.settings
    env = compat.build_training_env(settings.repo_root, [settings.secrets_file])
    names = [config] if config else compat.discover_configs(settings.repo_root)
    results = []
    for name in names:
        try:
            error = compat.validate_training_config(settings.repo_root, name, {}, env)
        except Exception as exc:
            error = str(exc)
        results.append({"config": name, "ready": error is None, "error": error})
    return ok({"results": results})
