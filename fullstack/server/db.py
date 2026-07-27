"""SQLite 连接、事务与 SQL 追踪。

两个 Windows 相关的注意点：
  * 所有文件读取显式指定 encoding="utf-8"（Windows 默认 cp936，读中文 .sql 会崩）
  * 路径一律用 pathlib，不拼字符串
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "db"
DB_PATH = DB_DIR / "ontology.db"


def read_sql(name: str) -> str:
    """读取 db/ 下的 .sql 文件。encoding 必须显式给，否则 Windows 上会 UnicodeDecodeError。"""
    return (DB_DIR / name).read_text(encoding="utf-8")


class SqlTrace:
    """记录一次请求里真正执行过的 SQL。

    这是这个项目的教学核心之一：界面上的「链接遍历」到底变成了什么 SQL，
    不用猜，直接看。
    """

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, sql: str, params: Iterable[Any], ms: float,
               rows: int | None = None, meta: bool = False) -> None:
        self.entries.append(
            {
                "sql": " ".join(sql.split()),
                "params": [_jsonable(p) for p in params],
                "ms": round(ms, 2),
                "rows": rows,
                # meta=True 的是读本体元数据的查询。它们跟业务无关，
                # 前端面板默认折叠，免得把真正有意思的那条 JOIN 淹掉。
                "meta": meta,
            }
        )

    def dump(self) -> list[dict[str, Any]]:
        return self.entries


def _jsonable(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


class Conn:
    """包一层 sqlite3.Connection，把每条 SQL 记进 trace。"""

    def __init__(self, raw: sqlite3.Connection, trace: SqlTrace) -> None:
        self.raw = raw
        self.trace = trace

    def query(self, sql: str, params: Iterable[Any] = (), meta: bool = False) -> list[sqlite3.Row]:
        params = tuple(params)
        t0 = time.perf_counter()
        rows = self.raw.execute(sql, params).fetchall()
        self.trace.record(sql, params, (time.perf_counter() - t0) * 1000, len(rows), meta)
        return rows

    def one(self, sql: str, params: Iterable[Any] = (), meta: bool = False) -> sqlite3.Row | None:
        rows = self.query(sql, params, meta)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """执行写入，返回受影响行数（乐观锁靠它判断冲突）。"""
        params = tuple(params)
        t0 = time.perf_counter()
        cur = self.raw.execute(sql, params)
        n = cur.rowcount
        self.trace.record(sql, params, (time.perf_counter() - t0) * 1000, n)
        return n

    @contextmanager
    def transaction(self):
        """一个 Action 的全部编辑要么一起成功，要么一起回滚。"""
        try:
            self.raw.execute("BEGIN IMMEDIATE")
            yield self
        except Exception:
            self.raw.rollback()
            raise
        else:
            self.raw.commit()


def connect(path: Path | None = None, trace: SqlTrace | None = None) -> Conn:
    raw = sqlite3.connect(
        str(path or DB_PATH),
        # FastAPI 的同步路由跑在线程池里，每个请求一条连接，这里不跨线程复用
        check_same_thread=False,
        isolation_level=None,  # 自己控制事务边界
        timeout=10.0,
    )
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("PRAGMA journal_mode = WAL")
    raw.execute("PRAGMA busy_timeout = 5000")
    return Conn(raw, trace or SqlTrace())
