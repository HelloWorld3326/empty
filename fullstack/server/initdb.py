"""建库脚本：删掉旧库，按 schema.sql 建表，灌入两份种子数据。

用法（在 fullstack 目录下）：
    python -m server.initdb

输出刻意只用 ASCII —— Windows 控制台默认是 GBK，打印中文可能抛
UnicodeEncodeError，把一个本来成功的建库过程弄成"失败"。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from .db import DB_PATH, read_sql


def init(db_path: Path | None = None, quiet: bool = False) -> Path:
    path = db_path or DB_PATH
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()

    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(read_sql("schema.sql"))
        conn.executescript(read_sql("seed_business.sql"))
        conn.executescript(read_sql("seed_ontology.sql"))
        conn.commit()

        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in (
                "buyer", "product", "orders", "order_line",
                "object_type", "object_property", "link_type", "action_type", "app_user",
            )
        }
    finally:
        conn.close()

    if not quiet:
        print(f"[ok] database created: {path}")
        for name, n in counts.items():
            print(f"     {name:<18} {n}")
    return path


if __name__ == "__main__":
    try:
        init()
    except Exception as exc:  # noqa: BLE001
        print(f"[fail] {exc}", file=sys.stderr)
        raise SystemExit(1)
