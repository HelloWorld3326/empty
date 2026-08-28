"""网关 → 业务工具 API 的调用（登记绑定关系、转人工建单）。"""
import datetime as dt
import logging

import httpx
import jwt

from .config import settings

logger = logging.getLogger("chat-gateway.tools")

DEFAULT_SCOPES = ["customer:read", "order:read", "ticket:read", "ticket:write"]


def _headers(chat_id: str = "") -> dict[str, str]:
    headers = {"X-Service-Key": settings.tool_api_service_key, "Content-Type": "application/json"}
    if chat_id:
        headers["X-Chat-Id"] = chat_id
    return headers


async def bind_session(chat_id: str, customer_id: str, scopes: list[str] | None = None) -> None:
    """把 MaxKB 会话和真实客户绑定。绑定失败必须让建会话整体失败，
    否则后面工作流查数据会拿不到身份，用户会看到莫名其妙的报错。"""
    url = settings.tool_api_base_url.rstrip("/") + "/internal/bind"
    payload = {"chat_id": chat_id, "customer_id": customer_id, "scopes": scopes or DEFAULT_SCOPES}
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()


async def create_ticket(chat_id: str, title: str, detail: str) -> dict:
    url = settings.tool_api_base_url.rstrip("/") + "/tools/customer/tickets"
    async with httpx.AsyncClient(timeout=8) as client:
        resp = await client.post(url, json={"title": title, "detail": detail}, headers=_headers(chat_id))
        resp.raise_for_status()
        return resp.json()


def issue_tool_token(customer_id: str, scopes: list[str] | None = None) -> str:
    """可选方案：签发短期令牌，通过对话变量传给工作流。
    没配 TOOL_TOKEN_SECRET 时返回空串，走会话绑定方案即可。"""
    if not settings.tool_token_secret:
        return ""
    now = dt.datetime.now(dt.timezone.utc)
    claims = {
        "sub": customer_id,
        "aud": settings.tool_token_audience,
        "scopes": scopes or DEFAULT_SCOPES,
        "iat": now,
        "exp": now + dt.timedelta(seconds=settings.tool_token_ttl),
    }
    return jwt.encode(claims, settings.tool_token_secret, algorithm="HS256")
