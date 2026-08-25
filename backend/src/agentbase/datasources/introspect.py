"""Schema 内省与缓存。

问数准确率的第一决定因素不是模型，是模型看到的 schema 质量。所以这里做两件事：
把库结构完整抓下来缓存，以及把它渲染成模型好读的形式（带上中文注释）。

字段注释缺失是国内业务库的常态。缺注释时不要假装有——渲染成
``(无注释)`` 让模型知道这里信息不足，比让它对着 ``t_ord_inf_02`` 硬猜要好。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import inspect

from ..config import DataSourceConfig
from .registry import DataSourceRegistry

logger = logging.getLogger(__name__)


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool = True
    comment: str | None = None
    primary_key: bool = False


@dataclass
class TableInfo:
    datasource: str
    schema: str | None
    name: str
    comment: str | None = None
    columns: list[ColumnInfo] = field(default_factory=list)
    foreign_keys: list[str] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name

    def to_card(self) -> str:
        """一行摘要，用于检索结果列表。"""
        return f"{self.qualified_name} — {self.comment or '(无注释)'}"

    def to_ddl(self) -> str:
        """给模型看的完整表结构。用类 DDL 的形式，模型对这个格式最熟。"""
        lines = [f"-- {self.comment}" if self.comment else "-- (该表无注释)"]
        lines.append(f"CREATE TABLE {self.qualified_name} (")
        for col in self.columns:
            bits = [f"  {col.name}", col.type]
            if col.primary_key:
                bits.append("PRIMARY KEY")
            if not col.nullable:
                bits.append("NOT NULL")
            line = " ".join(bits)
            if col.comment:
                line += f"  -- {col.comment}"
            lines.append(line + ",")
        if lines[-1].endswith(","):
            lines[-1] = lines[-1][:-1]
        lines.append(");")
        lines.extend(f"-- FK: {fk}" for fk in self.foreign_keys)
        return "\n".join(lines)


def introspect_datasource(
    registry: DataSourceRegistry, ds: DataSourceConfig
) -> list[TableInfo]:
    engine = registry.engine(ds.name)
    inspector = inspect(engine)
    schemas = ds.include_schemas or [None]  # None = 默认 schema
    excluded = {t.lower() for t in ds.exclude_tables}

    tables: list[TableInfo] = []
    for schema in schemas:
        for table_name in inspector.get_table_names(schema=schema):
            qualified = f"{schema}.{table_name}" if schema else table_name
            if table_name.lower() in excluded or qualified.lower() in excluded:
                continue
            tables.append(_read_table(inspector, ds.name, schema, table_name))
    return tables


def _read_table(inspector, datasource: str, schema: str | None, name: str) -> TableInfo:
    try:
        comment = (inspector.get_table_comment(name, schema=schema) or {}).get("text")
    except Exception as exc:
        # SQLite 等方言不支持表注释，没有就是没有，不该让整次内省失败。
        # 但要记下来——连不上库也会走到这里，全静默的话这种故障看不见。
        logger.debug("读取表注释失败 %s: %s", name, exc)
        comment = None

    pk_cols: set[str] = set()
    try:
        pk_cols = set((inspector.get_pk_constraint(name, schema=schema) or {}).get(
            "constrained_columns"
        ) or [])
    except Exception as exc:
        logger.debug("读取主键失败 %s: %s", name, exc)

    columns = [
        ColumnInfo(
            name=col["name"],
            type=str(col.get("type", "")),
            nullable=bool(col.get("nullable", True)),
            comment=col.get("comment"),
            primary_key=col["name"] in pk_cols,
        )
        for col in inspector.get_columns(name, schema=schema)
    ]

    fks: list[str] = []
    try:
        for fk in inspector.get_foreign_keys(name, schema=schema):
            local = ",".join(fk.get("constrained_columns") or [])
            ref_table = fk.get("referred_table")
            ref_cols = ",".join(fk.get("referred_columns") or [])
            if local and ref_table:
                fks.append(f"{local} -> {ref_table}({ref_cols})")
    except Exception as exc:
        logger.debug("读取外键失败 %s: %s", name, exc)

    return TableInfo(
        datasource=datasource,
        schema=schema,
        name=name,
        comment=comment,
        columns=columns,
        foreign_keys=fks,
    )


class SchemaCache:
    """落地成 JSON 文件。

    刻意不做成实时内省：上千张表的内省要几十秒，不能每次提问都跑一遍。
    库结构变更靠 ``/api/schema/refresh`` 手动或定时刷新。
    """

    def __init__(self, path: str | Path = ".cache/schema.json") -> None:
        self.path = Path(path)
        self._tables: dict[str, list[TableInfo]] = {}

    def refresh(
        self, registry: DataSourceRegistry, names: list[str] | None = None
    ) -> dict[str, int]:
        targets = names or registry.names()
        counts: dict[str, int] = {}
        for name in targets:
            ds = registry.config_for(name)
            tables = introspect_datasource(registry, ds)
            self._tables[name] = tables
            counts[name] = len(tables)
        self.save()
        return counts

    def tables(self, datasource: str | None = None) -> list[TableInfo]:
        if datasource:
            return list(self._tables.get(datasource, []))
        return [t for tables in self._tables.values() for t in tables]

    def find(self, datasource: str, qualified_name: str) -> TableInfo | None:
        wanted = qualified_name.lower()
        for table in self._tables.get(datasource, []):
            if table.qualified_name.lower() == wanted or table.name.lower() == wanted:
                return table
        return None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            name: [asdict(t) for t in tables] for name, tables in self._tables.items()
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> bool:
        if not self.path.exists():
            return False
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self._tables = {
            name: [
                TableInfo(
                    **{**t, "columns": [ColumnInfo(**c) for c in t.get("columns", [])]}
                )
                for t in tables
            ]
            for name, tables in payload.items()
        }
        return True
