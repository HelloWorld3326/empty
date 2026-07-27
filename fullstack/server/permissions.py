"""角色与权限。

演练场里完全没有这一层，而它恰恰是本体在真实组织里最重要的能力之一：
不是每个人都能跑每个操作。这里做的是最薄的一版 —— 没有真实认证，
用户切换就是个下拉框，靠 X-Actor 请求头传递。
"""

from __future__ import annotations

from dataclasses import dataclass

from .db import Conn

ADMIN_ROLE = "管理员"


class Forbidden(Exception):
    """越权。→ 403"""


@dataclass
class Actor:
    username: str
    display_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN_ROLE

    def public(self) -> dict[str, str]:
        return {"username": self.username, "cn": self.display_name, "role": self.role}


def load_actor(conn: Conn, username: str | None) -> Actor:
    row = None
    if username:
        row = conn.one("SELECT * FROM app_user WHERE username = ?", (username,), meta=True)
    if row is None:
        row = conn.one("SELECT * FROM app_user ORDER BY username LIMIT 1", meta=True)
    if row is None:
        return Actor("anonymous", "匿名", "访客")
    return Actor(row["username"], row["display_name"], row["role"])


def can_run(actor: Actor, required_role: str | None) -> bool:
    """管理员通吃；其余按角色精确匹配；没写要求的谁都能跑。"""
    if required_role in (None, ""):
        return True
    return actor.is_admin or actor.role == required_role


def require_action(actor: Actor, action_id: str, required_role: str | None) -> None:
    if not can_run(actor, required_role):
        raise Forbidden(
            f"{actor.display_name}（{actor.role}）没有权限执行「{action_id}」，"
            f"这个操作需要 {required_role}"
        )


def require_admin(actor: Actor, what: str = "改本体") -> None:
    if not actor.is_admin:
        raise Forbidden(f"{actor.display_name}（{actor.role}）没有权限{what}，这需要管理员")


def list_users(conn: Conn) -> list[dict[str, str]]:
    return [
        {"username": r["username"], "cn": r["display_name"], "role": r["role"]}
        for r in conn.query("SELECT * FROM app_user ORDER BY username", meta=True)
    ]
