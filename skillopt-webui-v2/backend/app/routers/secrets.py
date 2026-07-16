"""API 密钥管理 — 把"手工编辑 .env 文件"按钮化。

写入 <SKILLOPT_DATA_DIR>/secrets/webui.env（挂卷持久化），训练子进程通过
build_training_env() 自动继承；GET 只回显 key 名与掩码，绝不返回明文。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..auth import AuthDep
from ..errors import bad_request, ok

router = APIRouter(prefix="/settings", tags=["settings"])

# 常用密钥键的提示清单（来自 SkillOpt .env.example），前端渲染成带说明的表单
WELL_KNOWN_KEYS = [
    {"key": "AZURE_OPENAI_ENDPOINT", "label_zh": "Azure OpenAI Endpoint", "secret": False},
    {"key": "AZURE_OPENAI_API_KEY", "label_zh": "Azure OpenAI API Key", "secret": True},
    {"key": "AZURE_OPENAI_AUTH_MODE", "label_zh": "Azure 认证方式 (api_key/azure_cli/managed_identity)", "secret": False},
    {"key": "OPTIMIZER_AZURE_OPENAI_ENDPOINT", "label_zh": "优化器角色 Azure Endpoint（可选覆盖）", "secret": False},
    {"key": "TARGET_AZURE_OPENAI_ENDPOINT", "label_zh": "目标角色 Azure Endpoint（可选覆盖）", "secret": False},
    {"key": "QWEN_CHAT_BASE_URL", "label_zh": "Qwen/vLLM 服务地址", "secret": False},
    {"key": "MINIMAX_API_KEY", "label_zh": "MiniMax API Key", "secret": True},
    {"key": "ANTHROPIC_API_KEY", "label_zh": "Anthropic API Key", "secret": True},
]

_SECRET_MARKERS = ("key", "secret", "token", "password")


class PutSecretsRequest(BaseModel):
    entries: dict[str, str | None] = Field(
        description="要写入的键值；value 为 null 表示删除该键"
    )


def _read_entries(request: Request) -> dict[str, str]:
    path = request.app.state.settings.secrets_file
    entries: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                entries[key.strip()] = value.strip().strip("\"'")
    return entries


def _mask(key: str, value: str) -> str:
    if any(m in key.lower() for m in _SECRET_MARKERS):
        return "******"
    return value


@router.get("/secrets")
async def get_secrets(request: Request, _auth: dict = AuthDep):
    entries = _read_entries(request)
    return ok({
        "entries": [
            {"key": k, "value": _mask(k, v)} for k, v in sorted(entries.items())
        ],
        "well_known": WELL_KNOWN_KEYS,
    })


@router.put("/secrets")
async def put_secrets(body: PutSecretsRequest, request: Request, _auth: dict = AuthDep):
    entries = _read_entries(request)
    for key, value in body.entries.items():
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            raise bad_request(f"invalid env key: {key!r}")
        if value is None:
            entries.pop(key, None)
        elif value == "******":
            continue  # 掩码原样提交 = 未修改，保留旧值
        else:
            if "\n" in value or "\r" in value:
                raise bad_request(f"invalid env value for {key}: newlines not allowed")
            entries[key] = value
    path = request.app.state.settings.secrets_file
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{k}={v}\n" for k, v in sorted(entries.items()))
    tmp = path.with_suffix(".env.tmp")
    tmp.write_text(content)
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return ok({"count": len(entries)})
