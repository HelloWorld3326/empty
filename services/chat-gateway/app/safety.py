"""限流与输入处理。"""
import re
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, status

from .config import settings

_PHONE = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
_ID_CARD = re.compile(r"(?<![0-9A-Za-z])(\d{6})(\d{8})(\d{3}[0-9Xx])(?![0-9A-Za-z])")
_BANK_CARD = re.compile(r"(?<!\d)(\d{4})(\d{8,11})(\d{4})(?!\d)")


def sanitize_question(question: str) -> str:
    """裁剪超长输入，并把客户随手打出来的证件号、银行卡掩码掉再送云端模型。"""
    question = question.strip()
    if not question:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "问题不能为空")
    if len(question) > settings.max_question_chars:
        question = question[: settings.max_question_chars]
    if settings.mask_user_input:
        question = _ID_CARD.sub(lambda m: f"{m.group(1)}********{m.group(3)}", question)
        question = _BANK_CARD.sub(lambda m: f"{m.group(1)}{'*' * len(m.group(2))}{m.group(3)}", question)
        question = _PHONE.sub(lambda m: f"{m.group(1)}****{m.group(3)}", question)
    return question


class RateLimiter:
    """按客户维度的滑动窗口限流。

    演示用内存实现，多副本部署请换 Redis，否则限流会被副本数放大。
    """

    def __init__(self, per_minute: int):
        self._per_minute = per_minute
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.time()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self._per_minute:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "提问过于频繁，请稍后再试")
            hits.append(now)


limiter = RateLimiter(settings.rate_limit_per_minute)
