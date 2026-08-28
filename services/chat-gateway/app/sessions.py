"""网关侧的会话归属表。

作用：确保 A 客户拿到 B 客户的 conversation_id 也用不了。
演示用内存实现，多副本部署请换 Redis。
"""
import threading
import time
from dataclasses import dataclass


@dataclass
class Session:
    chat_id: str
    customer_id: str
    created_at: float


class SessionRegistry:
    def __init__(self, ttl_seconds: int = 8 * 3600):
        self._ttl = ttl_seconds
        self._data: dict[str, Session] = {}
        self._lock = threading.Lock()

    def add(self, chat_id: str, customer_id: str) -> Session:
        session = Session(chat_id, customer_id, time.time())
        with self._lock:
            self._data[chat_id] = session
        return session

    def get_for(self, chat_id: str, customer_id: str) -> Session | None:
        with self._lock:
            session = self._data.get(chat_id)
        if session is None or session.created_at + self._ttl < time.time():
            return None
        # 会话必须属于当前登录的这个客户
        return session if session.customer_id == customer_id else None


registry = SessionRegistry()
