"""工具 API 的鉴权：先确认调用方是 MaxKB，再确认它能代表哪个客户。"""
import hmac

import jwt
from fastapi import Header, HTTPException, status

from .config import settings
from .store import store, Binding

DEFAULT_SCOPES = ("customer:read", "order:read", "ticket:read", "ticket:write")


def verify_service_key(x_service_key: str = Header(default="")) -> None:
    """第一道门：只有配置了正确 Service Key 的 MaxKB HTTP 节点才能进来。"""
    if not hmac.compare_digest(x_service_key, settings.service_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid service key")


def _from_tool_token(token: str) -> Binding | None:
    """可选方案：网关签发的短期 JWT，通过对话变量传给工作流。"""
    if not token or not settings.token_secret:
        return None
    try:
        claims = jwt.decode(
            token, settings.token_secret, algorithms=["HS256"], audience=settings.token_audience
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid tool token: {exc}") from exc
    return Binding(claims["sub"], tuple(claims.get("scopes", DEFAULT_SCOPES)), 0)


def resolve_caller(
    x_service_key: str = Header(default=""),
    x_chat_id: str = Header(default=""),
    x_tool_token: str = Header(default=""),
) -> Binding:
    """第二道门：解析出本次调用允许访问哪个客户。

    两种来源，任选其一在 MaxKB 的 HTTP 节点里配置：
      1) X-Chat-Id: {{chat_id}}      —— 走网关预先登记的绑定关系（推荐，兼容各版本）
      2) X-Tool-Token: {{tool_token}} —— 走网关签发的短期令牌（需要工作流支持自定义变量）

    注意：这里绝不接受请求体或查询参数里的 customer_id，
    否则大模型就能自己编一个客户 ID 去查别人的数据。
    """
    verify_service_key(x_service_key)

    binding = _from_tool_token(x_tool_token)
    if binding is not None:
        return binding

    if not x_chat_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing X-Chat-Id or X-Tool-Token")

    binding = store.resolve(x_chat_id)
    if binding is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "会话未绑定客户身份或已过期，请重新发起会话")
    return binding


def require_scope(binding: Binding, scope: str) -> None:
    if scope not in binding.scopes:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"缺少权限: {scope}")
