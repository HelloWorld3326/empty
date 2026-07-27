"""读本体元数据，构建"对象类型 ↔ 业务表"的映射。

这个模块回答的是：一个叫 Order 的对象类型，究竟对应哪张表、
它的 status 属性来自哪一列、placed 链接由哪个外键实现。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .db import Conn


@dataclass
class Property:
    api_name: str
    display_name: str
    data_type: str
    source_column: str
    value_map: dict[str, str] | None = None
    options: list[str] | None = None
    scale: int | None = None
    editable: bool = True

    @property
    def reverse_map(self) -> dict[str, str]:
        """显示值 → 库里的码值。写入时要用。"""
        return {v: k for k, v in (self.value_map or {}).items()}

    def to_display(self, raw: Any) -> Any:
        """库里的值 → 给人看的值。"""
        if raw is None:
            return None
        if self.value_map:
            return self.value_map.get(str(raw), raw)
        if self.scale:
            return raw / self.scale
        if self.data_type == "boolean":
            return bool(raw)
        return raw

    def to_storage(self, value: Any) -> Any:
        """给人看的值 → 库里的值。写入路径必须和上面严格互逆。"""
        if value is None:
            return None
        if self.value_map:
            return self.reverse_map.get(str(value), value)
        if self.scale:
            # 必须 round：899.99 * 100 在浮点下是 89998.99999...，int() 会少一分
            return int(round(float(value) * self.scale))
        if self.data_type in ("integer",):
            return int(value)
        if self.data_type == "boolean":
            return 1 if value else 0
        return value

    def public(self) -> dict[str, Any]:
        return {
            "id": self.api_name,
            "cn": self.display_name,
            "type": self.data_type,
            "sourceColumn": self.source_column,
            "options": self.options,
            "editable": self.editable,
            "scale": self.scale,
            "valueMap": self.value_map,
        }


@dataclass
class ObjectType:
    api_name: str
    display_name: str
    source_table: str
    pk_column: str
    title_prop: str | None
    color: int
    id_prefix: str | None
    next_seq: int
    props: list[Property] = field(default_factory=list)
    has_row_version: bool = False

    def prop(self, api_name: str) -> Property | None:
        return next((p for p in self.props if p.api_name == api_name), None)

    def column_of(self, api_name: str) -> str | None:
        p = self.prop(api_name)
        return p.source_column if p else None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.api_name,
            "cn": self.display_name,
            "sourceTable": self.source_table,
            "pkColumn": self.pk_column,
            "titleProp": self.title_prop,
            "color": self.color,
            "idPrefix": self.id_prefix,
            "hasRowVersion": self.has_row_version,
            "props": [p.public() for p in self.props],
        }


@dataclass
class LinkType:
    api_name: str
    from_type: str
    to_type: str
    cardinality: str
    fwd_label: str
    inv_label: str
    fk_table: str
    fk_column: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.api_name,
            "from": self.from_type,
            "to": self.to_type,
            "card": self.cardinality,
            "fwd": self.fwd_label,
            "inv": self.inv_label,
            "fkTable": self.fk_table,
            "fkColumn": self.fk_column,
        }


@dataclass
class ActionType:
    api_name: str
    display_name: str
    description: str
    params: list[dict[str, Any]]
    rules: list[dict[str, Any]]
    edits: list[dict[str, Any]]
    required_role: str | None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.api_name,
            "cn": self.display_name,
            "desc": self.description,
            "params": self.params,
            "rules": self.rules,
            "edits": self.edits,
            "requiredRole": self.required_role,
        }


@dataclass
class Ontology:
    object_types: list[ObjectType]
    link_types: list[LinkType]
    action_types: list[ActionType]

    def ot(self, api_name: str) -> ObjectType | None:
        return next((t for t in self.object_types if t.api_name == api_name), None)

    def lt(self, api_name: str) -> LinkType | None:
        return next((l for l in self.link_types if l.api_name == api_name), None)

    def at(self, api_name: str) -> ActionType | None:
        return next((a for a in self.action_types if a.api_name == api_name), None)

    def links_of(self, type_name: str) -> list[tuple[LinkType, str]]:
        """一个对象类型能走的所有链接，带方向。正向反向都要能走。"""
        out: list[tuple[LinkType, str]] = []
        for l in self.link_types:
            if l.from_type == type_name:
                out.append((l, "fwd"))
            if l.to_type == type_name:
                out.append((l, "inv"))
        return out

    def public(self) -> dict[str, Any]:
        return {
            "objectTypes": [t.public() for t in self.object_types],
            "linkTypes": [l.public() for l in self.link_types],
            "actionTypes": [a.public() for a in self.action_types],
        }


def _loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def load(conn: Conn) -> Ontology:
    """把元数据表读成内存里的本体对象。"""
    object_types: list[ObjectType] = []
    for r in conn.query("SELECT * FROM object_type ORDER BY sort_order, api_name", meta=True):
        cols = {c["name"] for c in conn.query(f"PRAGMA table_info({r['source_table']})", meta=True)}
        ot = ObjectType(
            api_name=r["api_name"],
            display_name=r["display_name"],
            source_table=r["source_table"],
            pk_column=r["pk_column"],
            title_prop=r["title_prop"],
            color=r["color"],
            id_prefix=r["id_prefix"],
            next_seq=r["next_seq"],
            has_row_version="row_version" in cols,
        )
        for p in conn.query(
            "SELECT * FROM object_property WHERE object_type = ? ORDER BY sort_order, api_name",
            (ot.api_name,), meta=True,
        ):
            ot.props.append(
                Property(
                    api_name=p["api_name"],
                    display_name=p["display_name"],
                    data_type=p["data_type"],
                    source_column=p["source_column"],
                    value_map=_loads(p["value_map"], None),
                    options=_loads(p["options"], None),
                    scale=p["scale"],
                    editable=bool(p["editable"]),
                )
            )
        object_types.append(ot)

    link_types = [
        LinkType(
            api_name=r["api_name"],
            from_type=r["from_type"],
            to_type=r["to_type"],
            cardinality=r["cardinality"],
            fwd_label=r["fwd_label"],
            inv_label=r["inv_label"],
            fk_table=r["fk_table"],
            fk_column=r["fk_column"],
        )
        for r in conn.query("SELECT * FROM link_type ORDER BY sort_order, api_name", meta=True)
    ]

    action_types = [
        ActionType(
            api_name=r["api_name"],
            display_name=r["display_name"],
            description=r["description"] or "",
            params=_loads(r["params"], []),
            rules=_loads(r["rules"], []),
            edits=_loads(r["edits"], []),
            required_role=r["required_role"],
        )
        for r in conn.query("SELECT * FROM action_type ORDER BY sort_order, api_name", meta=True)
    ]

    return Ontology(object_types, link_types, action_types)


def list_tables(conn: Conn) -> list[dict[str, Any]]:
    """数据库里有哪些业务表和列 —— 建模时要靠它来选映射。"""
    skip = {
        "object_type", "object_property", "link_type", "action_type",
        "app_user", "action_log", "schema_log", "sqlite_sequence",
    }
    out = []
    for t in conn.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", meta=True):
        name = t["name"]
        if name in skip or name.startswith("sqlite_"):
            continue
        cols = [
            {"name": c["name"], "type": c["type"], "pk": bool(c["pk"]), "notnull": bool(c["notnull"])}
            for c in conn.query(f"PRAGMA table_info({name})", meta=True)
        ]
        fks = [
            {"column": f["from"], "refTable": f["table"], "refColumn": f["to"]}
            for f in conn.query(f"PRAGMA foreign_key_list({name})", meta=True)
        ]
        out.append({"name": name, "columns": cols, "foreignKeys": fks})
    return out
