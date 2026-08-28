"""数据访问层。

三种数据源可切换，接口保持一致，所以换数据源不影响上层：
  - mock : 内置示例数据，用来先把链路跑通
  - db   : 直连数据库（SQLAlchemy，全部参数化查询）
  - http : 转调你们客户360 已有的内部接口

每个方法的第一个参数都是 customer_id，且**必须**作为过滤条件参与查询。
这是防越权的最后一道防线，改这里的代码要格外小心。
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

from .config import settings

# --------------------------------------------------------------------------- mock

_MOCK_CUSTOMERS: dict[str, dict[str, Any]] = {
    "C10001": {
        "customer_id": "C10001",
        "name": "张三",
        "level": "黄金会员",
        "phone": "13800138000",
        "register_date": "2021-03-14",
        "points": 12800,
    },
    "C10002": {
        "customer_id": "C10002",
        "name": "李四",
        "level": "普通会员",
        "phone": "13900139001",
        "register_date": "2024-08-02",
        "points": 320,
    },
}

_MOCK_ORDERS: dict[str, list[dict[str, Any]]] = {
    "C10001": [
        {
            "order_no": "SO2026080100123",
            "status": "退款中",
            "amount": 2599.00,
            "created_at": "2026-08-01 10:22:31",
            "items": ["降噪耳机 Pro"],
            "refund_reason": "七天无理由",
            "refund_stage": "已收到退货，财务复核中",
            "expected_refund_date": "2026-08-30",
        },
        {
            "order_no": "SO2026071900088",
            "status": "已完成",
            "amount": 399.00,
            "created_at": "2026-07-19 20:05:00",
            "items": ["机械键盘"],
        },
    ],
    "C10002": [
        {
            "order_no": "SO2026082600301",
            "status": "已发货",
            "amount": 129.00,
            "created_at": "2026-08-26 09:12:44",
            "items": ["手机壳"],
            "tracking_no": "SF1234567890",
        }
    ],
}

_MOCK_TICKETS: dict[str, list[dict[str, Any]]] = {
    "C10001": [
        {
            "ticket_no": "TK20260802001",
            "title": "退款进度咨询",
            "status": "处理中",
            "created_at": "2026-08-02 11:00:00",
            "owner": "客服-王五",
        }
    ]
}

_MOCK_TICKET_SEQ = [1000]


# --------------------------------------------------------------------------- 对外接口


class Repository:
    def get_profile(self, customer_id: str) -> dict[str, Any] | None:
        if settings.data_source == "mock":
            return _MOCK_CUSTOMERS.get(customer_id)
        if settings.data_source == "db":
            rows = self._query(
                "SELECT customer_id, name, level, phone, register_date, points "
                "FROM customer WHERE customer_id = :cid",
                {"cid": customer_id},
            )
            return rows[0] if rows else None
        return self._get_upstream(f"/customers/{customer_id}")

    def list_orders(self, customer_id: str, status: str | None, limit: int) -> list[dict[str, Any]]:
        if settings.data_source == "mock":
            orders = _MOCK_ORDERS.get(customer_id, [])
            if status:
                orders = [o for o in orders if o["status"] == status]
            return orders[:limit]
        if settings.data_source == "db":
            sql = (
                "SELECT order_no, status, amount, created_at FROM sales_order "
                "WHERE customer_id = :cid"
            )
            params: dict[str, Any] = {"cid": customer_id, "limit": limit}
            if status:
                sql += " AND status = :status"
                params["status"] = status
            sql += " ORDER BY created_at DESC LIMIT :limit"
            return self._query(sql, params)
        return self._get_upstream(f"/customers/{customer_id}/orders", {"status": status, "limit": limit}) or []

    def get_order(self, customer_id: str, order_no: str) -> dict[str, Any] | None:
        if settings.data_source == "mock":
            return next((o for o in _MOCK_ORDERS.get(customer_id, []) if o["order_no"] == order_no), None)
        if settings.data_source == "db":
            # customer_id 与 order_no 同时作为条件：查别人的订单号也查不出来
            rows = self._query(
                "SELECT * FROM sales_order WHERE customer_id = :cid AND order_no = :ono",
                {"cid": customer_id, "ono": order_no},
            )
            return rows[0] if rows else None
        return self._get_upstream(f"/customers/{customer_id}/orders/{order_no}")

    def list_tickets(self, customer_id: str, limit: int) -> list[dict[str, Any]]:
        if settings.data_source == "mock":
            return _MOCK_TICKETS.get(customer_id, [])[:limit]
        if settings.data_source == "db":
            return self._query(
                "SELECT ticket_no, title, status, created_at FROM service_ticket "
                "WHERE customer_id = :cid ORDER BY created_at DESC LIMIT :limit",
                {"cid": customer_id, "limit": limit},
            )
        return self._get_upstream(f"/customers/{customer_id}/tickets", {"limit": limit}) or []

    def create_ticket(self, customer_id: str, title: str, detail: str, chat_id: str) -> dict[str, Any]:
        if settings.data_source == "mock":
            _MOCK_TICKET_SEQ[0] += 1
            ticket = {
                "ticket_no": f"TK{dt.datetime.now():%Y%m%d}{_MOCK_TICKET_SEQ[0]}",
                "title": title,
                "status": "待受理",
                "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "detail": detail,
                "chat_id": chat_id,
            }
            _MOCK_TICKETS.setdefault(customer_id, []).insert(0, ticket)
            return ticket
        if settings.data_source == "db":
            raise NotImplementedError("写操作请接你们的工单服务，不要让 AI 直接写库")
        return self._post_upstream(
            "/tickets",
            {"customer_id": customer_id, "title": title, "detail": detail, "chat_id": chat_id},
        )

    # ----------------------------------------------------------------- 内部实现

    def _query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            from sqlalchemy import create_engine, text
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("使用 db 数据源需要先安装 sqlalchemy 及对应驱动") from exc

        engine = _get_engine(create_engine)
        with engine.connect() as conn:
            result = conn.execute(text(sql), params)
            return [dict(row) for row in result.mappings()]

    def _get_upstream(self, path: str, params: dict[str, Any] | None = None):
        with httpx.Client(timeout=settings.upstream_timeout) as client:
            resp = client.get(
                settings.upstream_base_url.rstrip("/") + path,
                params={k: v for k, v in (params or {}).items() if v is not None},
                headers=self._upstream_headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    def _post_upstream(self, path: str, payload: dict[str, Any]):
        with httpx.Client(timeout=settings.upstream_timeout) as client:
            resp = client.post(
                settings.upstream_base_url.rstrip("/") + path,
                json=payload,
                headers=self._upstream_headers(),
            )
            resp.raise_for_status()
            return resp.json()

    def _upstream_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if settings.upstream_token:
            headers["Authorization"] = f"Bearer {settings.upstream_token}"
        return headers


_engine_cache: list[Any] = []


def _get_engine(create_engine):
    if not _engine_cache:
        _engine_cache.append(create_engine(settings.db_dsn, pool_pre_ping=True, pool_size=5))
    return _engine_cache[0]


repository = Repository()
