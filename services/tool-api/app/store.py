"""会话 → 客户 的绑定关系。

这是整套方案的安全核心：MaxKB 工作流只知道自己的 chat_id，
customer_id 由 chat-gateway 在建会话时根据登录态写入，模型无法自行指定要查谁。

演示用内存实现，单进程有效。生产环境请换成 Redis（见 RedisBindingStore 注释），
否则网关多副本部署时会话会绑定不上。
"""
import threading
import time
from dataclasses import dataclass


@dataclass
class Binding:
    customer_id: str
    scopes: tuple
    expires_at: float


class InMemoryBindingStore:
    def __init__(self, ttl_seconds: int = 8 * 3600):
        self._ttl = ttl_seconds
        self._data: dict[str, Binding] = {}
        self._lock = threading.Lock()

    def bind(self, chat_id: str, customer_id: str, scopes: list[str]) -> None:
        with self._lock:
            self._purge_locked()
            self._data[chat_id] = Binding(customer_id, tuple(scopes), time.time() + self._ttl)

    def resolve(self, chat_id: str) -> Binding | None:
        with self._lock:
            binding = self._data.get(chat_id)
            if binding is None:
                return None
            if binding.expires_at < time.time():
                self._data.pop(chat_id, None)
                return None
            return binding

    def unbind(self, chat_id: str) -> None:
        with self._lock:
            self._data.pop(chat_id, None)

    def _purge_locked(self) -> None:
        now = time.time()
        for key in [k for k, v in self._data.items() if v.expires_at < now]:
            self._data.pop(key, None)


# 生产实现示例（需要 redis 依赖）：
#
# class RedisBindingStore:
#     def __init__(self, client, ttl_seconds=8 * 3600, prefix="maxkb:bind:"):
#         self._c, self._ttl, self._p = client, ttl_seconds, prefix
#
#     def bind(self, chat_id, customer_id, scopes):
#         self._c.setex(self._p + chat_id, self._ttl,
#                       json.dumps({"customer_id": customer_id, "scopes": scopes}))
#
#     def resolve(self, chat_id):
#         raw = self._c.get(self._p + chat_id)
#         if not raw:
#             return None
#         d = json.loads(raw)
#         return Binding(d["customer_id"], tuple(d["scopes"]), time.time() + self._ttl)

store = InMemoryBindingStore()
