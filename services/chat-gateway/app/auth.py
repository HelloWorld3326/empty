"""确认"现在提问的是哪个客户"。

这是整套方案安全性的起点：customer_id 只能从这里产出，
不接受前端传参，也不接受大模型输出。
"""
import logging

import httpx
import jwt
from fastapi import Header, HTTPException, status

from .config import settings

logger = logging.getLogger("chat-gateway.auth")


def current_customer_id(
    authorization: str = Header(default=""),
    x_debug_customer_id: str = Header(default=""),
) -> str:
    if settings.auth_mode == "debug":
        if not x_debug_customer_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少 X-Debug-Customer-Id（仅联调可用）")
        return x_debug_customer_id

    token = _bearer(authorization)

    if settings.auth_mode == "jwt":
        try:
            claims = jwt.decode(
                token,
                settings.auth_jwt_secret,
                algorithms=[a.strip() for a in settings.auth_jwt_algorithms.split(",")],
                audience=settings.auth_jwt_audience or None,
                options={"verify_aud": bool(settings.auth_jwt_audience)},
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录态无效") from exc
        customer_id = str(claims.get(settings.auth_jwt_customer_claim) or "")
        if not customer_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录态中没有客户标识")
        return customer_id

    # introspect：把 token 交给客户360 校验，由它回传客户标识
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.post(settings.auth_introspect_url, json={"token": token})
    except httpx.HTTPError as exc:
        logger.warning("调用客户360 校验接口失败: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "身份校验服务不可用") from exc
    if resp.status_code != 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录态无效")
    data = resp.json()
    customer_id = str(data.get(settings.auth_jwt_customer_claim) or data.get("customer_id") or "")
    if not customer_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录态中没有客户标识")
    return customer_id


def _bearer(authorization: str) -> str:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少 Authorization: Bearer <token>")
    return authorization[7:].strip()
