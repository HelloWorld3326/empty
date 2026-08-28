"""业务工具 API —— 供 MaxKB 工作流的 HTTP 请求节点调用。

设计原则：
  1. 所有接口都不接受 customer_id 参数，客户身份只能从会话绑定或 tool_token 推导；
  2. 默认只读，写操作（建工单）单独授权；
  3. 返回内容脱敏后再交给大模型。
"""
import logging

from fastapi import Depends, FastAPI, HTTPException, Header, status
from pydantic import BaseModel, Field

from .config import settings
from .masking import mask_payload
from .repository import repository
from .security import DEFAULT_SCOPES, require_scope, resolve_caller, verify_service_key
from .store import Binding, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("tool-api")

app = FastAPI(title="客服 Agent 业务工具 API", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    settings.validate()
    logger.info("tool-api 启动，数据源=%s 脱敏=%s", settings.data_source, settings.mask_pii)


def _out(payload):
    return mask_payload(payload) if settings.mask_pii else payload


@app.get("/healthz")
def healthz():
    return {"status": "ok", "data_source": settings.data_source}


# ---------------------------------------------------------------- 内部登记接口


class BindRequest(BaseModel):
    chat_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))


@app.post("/internal/bind", dependencies=[Depends(verify_service_key)])
def bind_session(req: BindRequest):
    """由 chat-gateway 在创建会话时调用，把 MaxKB 会话和真实客户绑定起来。

    这个接口只对内网开放，绝不能暴露到公网，否则等于把越权的口子敞开。
    """
    store.bind(req.chat_id, req.customer_id, req.scopes)
    logger.info("bind chat_id=%s customer_id=%s", req.chat_id, req.customer_id)
    return {"bound": True}


@app.post("/internal/unbind", dependencies=[Depends(verify_service_key)])
def unbind_session(chat_id: str):
    store.unbind(chat_id)
    return {"bound": False}


# ---------------------------------------------------------------- 工具接口


@app.get("/tools/customer/profile")
def get_profile(caller: Binding = Depends(resolve_caller)):
    require_scope(caller, "customer:read")
    profile = repository.get_profile(caller.customer_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "客户不存在")
    return _out(profile)


@app.get("/tools/customer/orders")
def list_orders(status_filter: str | None = None, limit: int = 5, caller: Binding = Depends(resolve_caller)):
    require_scope(caller, "order:read")
    limit = max(1, min(limit, 20))
    return _out({"orders": repository.list_orders(caller.customer_id, status_filter, limit)})


@app.get("/tools/customer/orders/{order_no}")
def get_order(order_no: str, caller: Binding = Depends(resolve_caller)):
    require_scope(caller, "order:read")
    order = repository.get_order(caller.customer_id, order_no)
    if order is None:
        # 不区分"订单不存在"和"订单不属于你"，避免被拿来枚举别人的订单号
        raise HTTPException(status.HTTP_404_NOT_FOUND, "未找到该订单")
    return _out(order)


@app.get("/tools/customer/tickets")
def list_tickets(limit: int = 5, caller: Binding = Depends(resolve_caller)):
    require_scope(caller, "ticket:read")
    return _out({"tickets": repository.list_tickets(caller.customer_id, max(1, min(limit, 20)))})


class CreateTicketRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    detail: str = Field(default="", max_length=2000)


@app.post("/tools/customer/tickets")
def create_ticket(
    req: CreateTicketRequest,
    x_chat_id: str = Header(default=""),
    caller: Binding = Depends(resolve_caller),
):
    """转人工 / 建工单。唯一的写操作，单独用 ticket:write 授权。"""
    require_scope(caller, "ticket:write")
    ticket = repository.create_ticket(caller.customer_id, req.title, req.detail, x_chat_id)
    logger.info("create_ticket customer_id=%s ticket=%s", caller.customer_id, ticket.get("ticket_no"))
    return _out(ticket)
