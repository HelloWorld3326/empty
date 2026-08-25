"""数据源注册表与查询执行。

**所有数据库连接都在网关进程里发生，凭证绝不进沙箱。**

沙箱里跑着 agent 生成的任意 bash，凡是沙箱能读到的东西都要当成已泄露。
所以 ``run_sql`` 是一个网关侧工具：模型只递进来一条 SQL，拿回来的是行数据，
数据库地址和密码它从头到尾看不到。明细结果落成沙箱里的文件，供后续
pandas 处理和用户下载。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ..config import Config, DataSourceConfig, RoleConfig
from .guard import GuardedSQL, guard_sql

# 行级权限的钩子：给定 (数据源, 表名, 角色) 返回一段追加的 WHERE 谓词。
# MVP 不实现，权限升到 c 档（行级/列级）时在这里挂实现，不用改调用方。
RowFilterHook = Callable[[str, str, RoleConfig | None], str | None]


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    row_count: int
    truncated_for_model: bool
    elapsed_ms: int
    sql: str
    limit_applied: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_model_text(self, max_rows: int) -> str:
        """渲染成给模型看的文本。

        刻意不把全量结果喂回模型：一来问数结果可能是客户名单、订单明细这类
        敏感数据，回传等于发到模型服务商；二来几千行进 prompt 纯属烧钱。
        明细在沙箱文件里，需要加工就用 python 读。
        """
        shown = self.rows[:max_rows]
        header = " | ".join(self.columns)
        sep = "-" * len(header)
        body = "\n".join(" | ".join("NULL" if v is None else str(v) for v in r) for r in shown)
        parts = [f"共 {self.row_count} 行，耗时 {self.elapsed_ms}ms", header, sep, body]
        if len(self.rows) > max_rows:
            parts.append(
                f"...（仅显示前 {max_rows} 行；完整结果已写入沙箱文件，用 python 读取处理）"
            )
        parts.extend(self.notes)
        return "\n".join(p for p in parts if p)


class DataSourceRegistry:
    """按需创建并缓存 SQLAlchemy engine。"""

    def __init__(self, config: Config, row_filter: RowFilterHook | None = None) -> None:
        self._config = config
        self._engines: dict[str, Engine] = {}
        self._row_filter = row_filter

    def names(self) -> list[str]:
        return [ds.name for ds in self._config.datasources]

    def describe_all(self, role: RoleConfig | None = None) -> str:
        lines = []
        for ds in self._config.datasources:
            if role and role.datasources and ds.name not in role.datasources:
                continue
            lines.append(f"- {ds.name}: {ds.description or '(无描述)'}")
        return "\n".join(lines) if lines else "(当前角色没有可访问的数据源)"

    def config_for(self, name: str) -> DataSourceConfig:
        return self._config.datasource(name)

    def engine(self, name: str) -> Engine:
        if name not in self._engines:
            ds = self._config.datasource(name)
            self._engines[name] = create_engine(
                ds.dsn,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=5,
                # 只读账号是第一道防线，这里是第二道。
                execution_options={"postgresql_readonly": ds.readonly} if _is_pg(ds.dsn) else {},
            )
        return self._engines[name]

    def check_access(self, name: str, role: RoleConfig | None) -> None:
        if role is None:
            return
        if role.datasources and name not in role.datasources:
            raise PermissionError(
                f"角色 `{role.name}` 无权访问数据源 `{name}`。"
                f"可访问: {', '.join(role.datasources) or '(无)'}"
            )

    def prepare(self, name: str, sql: str, role: RoleConfig | None = None) -> GuardedSQL:
        """只校验不执行 —— 人工确认环节要先把规范化后的 SQL 摆给用户看。"""
        self.check_access(name, role)
        ds = self._config.datasource(name)
        return guard_sql(
            sql,
            dsn=ds.dsn,
            max_rows=ds.max_rows,
            table_allowlist=role.table_allowlist if role else None,
        )

    def execute(
        self,
        name: str,
        sql: str,
        role: RoleConfig | None = None,
        *,
        prepared: GuardedSQL | None = None,
    ) -> QueryResult:
        ds = self._config.datasource(name)
        guarded = prepared or self.prepare(name, sql, role)

        started = time.perf_counter()
        with self.engine(name).connect() as conn:
            for stmt in _timeout_statements(ds):
                conn.exec_driver_sql(stmt)
            cursor = conn.execute(text(guarded.sql))
            columns = list(cursor.keys())
            rows = [tuple(r) for r in cursor.fetchall()]
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        notes: list[str] = []
        if guarded.limit_applied is not None and len(rows) >= guarded.limit_applied:
            notes.append(
                f"⚠ 结果被 LIMIT {guarded.limit_applied} 截断，实际可能更多。"
                "如果这是聚合类问题，请改写 SQL 用 GROUP BY 而不是拉全量明细。"
            )

        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated_for_model=len(rows) > ds.max_rows_to_model,
            elapsed_ms=elapsed_ms,
            sql=guarded.sql,
            limit_applied=guarded.limit_applied,
            notes=notes,
        )

    def dispose(self) -> None:
        for engine in self._engines.values():
            engine.dispose()
        self._engines.clear()


def _is_pg(dsn: str) -> bool:
    return dsn.lower().startswith(("postgres", "postgresql"))


def _timeout_statements(ds: DataSourceConfig) -> list[str]:
    """语句超时。跑飞的 SQL 能把业务库拖垮，这条不是可选项。"""
    ms = ds.statement_timeout_seconds * 1000
    dsn = ds.dsn.lower()
    if dsn.startswith(("postgres", "postgresql")):
        stmts = [f"SET statement_timeout = {ms}"]
        if ds.readonly:
            stmts.append("SET default_transaction_read_only = on")
        return stmts
    if dsn.startswith(("mysql", "mariadb")):
        return [f"SET SESSION max_execution_time = {ms}"]
    return []
