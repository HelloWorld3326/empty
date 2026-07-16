"""认证依赖 — SKILLOPT_AUTH_MODE=none|jwt。

jwt 模式校验 ``Authorization: Bearer <token>``（HS256）。与 nexent 打通时把
SKILLOPT_JWT_SECRET 配成 nexent 的 Supabase JWT secret 即可：nexent 网关
(frontend/server.js) 代理请求时会自动把 HttpOnly cookie 中的 token 注入
Authorization 头，本服务校验的是同一份 token（对齐 nexent
backend/utils/auth_utils.py 的 HS256 + 忽略 audience 行为）。
"""
from __future__ import annotations

from fastapi import Depends, Header, Request

from .errors import unauthorized
from .settings import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    settings: Settings = request.app.state.settings
    if settings.auth_mode != "jwt":
        return {"user_id": "anonymous"}

    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized("missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        import jwt as pyjwt

        payload = pyjwt.decode(
            token,
            settings.jwt_secret,
            algorithms=settings.jwt_algorithms,
            options={"verify_aud": False},
        )
    except Exception as exc:
        raise unauthorized(f"invalid token: {exc}")
    return {"user_id": payload.get("sub", "unknown"), "claims": payload}


AuthDep = Depends(require_auth)
