"""审计日志。对外部客户提供服务，问了什么、查了谁的数据必须留痕。"""
import json
import os
import threading
import time

from .config import settings

_lock = threading.Lock()


def record(event: str, **fields) -> None:
    entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **fields}
    line = json.dumps(entry, ensure_ascii=False)
    path = settings.audit_log_path
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        # 审计写盘失败不能影响对话主流程，降级到标准输出
        print("[audit]", line, flush=True)
