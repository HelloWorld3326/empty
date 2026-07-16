"""benchmark 预设列表与配置表单 schema。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import config_meta
from .. import skillopt_compat as compat
from ..auth import AuthDep
from ..errors import not_found, ok

router = APIRouter(prefix="/configs", tags=["configs"])


@router.get("")
async def list_configs(request: Request, _auth: dict = AuthDep):
    settings = request.app.state.settings
    return ok({"configs": compat.discover_configs(settings.repo_root)})


@router.get("/schema")
async def schema(_auth: dict = AuthDep):
    """全局表单 schema（字段元数据），与具体预设无关。"""
    return ok(config_meta.form_schema())


@router.get("/{name:path}")
async def get_config(name: str, request: Request, _auth: dict = AuthDep):
    """返回某预设解析后的完整配置（_base_ 继承已合并）+ 表单 schema。

    values 为元数据覆盖的字段值（渲染成表单）；extra 为其余字段
    （benchmark 特有的 env 透传键等，渲染进高级 YAML 编辑器）；
    密钥字段已脱敏。
    """
    settings = request.app.state.settings
    try:
        cfg = compat.load_merged_config(settings.repo_root, name)
    except (FileNotFoundError, ValueError) as exc:
        raise not_found(str(exc))
    cfg = compat.redact_config(cfg)
    known, unknown = config_meta.split_known_unknown(cfg)
    return ok({
        "name": name,
        "values": known,
        "extra": unknown,
        "schema": config_meta.form_schema(),
    })
