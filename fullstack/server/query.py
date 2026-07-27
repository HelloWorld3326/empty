"""对象查询与链接遍历 —— 本体的读路径。

这里是"沿着链接走"真正变成 SQL 的地方。界面上点一下「所属订单」，
底下跑的就是本文件生成的那条 JOIN。
"""

from __future__ import annotations

import re
from typing import Any

from .db import Conn
from .ontology import LinkType, ObjectType, Ontology

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ident(name: str) -> str:
    """表名/列名要拼进 SQL，必须先验一遍。

    这些名字来自本体元数据，而元数据是管理员可改的 —— 不验就是注入口子。
    """
    if not _IDENT.match(name or ""):
        raise ValueError(f"非法标识符: {name!r}")
    return name


# ---------------------------------------------------------------------
#  行 → 对象
# ---------------------------------------------------------------------

def row_to_object(ot: ObjectType, row: Any) -> dict[str, Any]:
    """把一行数据库记录翻译成一个本体对象：列名换成属性名，码值换成显示值。"""
    keys = row.keys()
    props: dict[str, Any] = {}
    for p in ot.props:
        if p.source_column in keys:
            props[p.api_name] = p.to_display(row[p.source_column])
    pk = row[ot.pk_column]
    title = props.get(ot.title_prop or "", None)
    return {
        "type": ot.api_name,
        "pk": pk,
        "title": str(title) if title not in (None, "") else str(pk),
        "props": props,
        "rowVersion": row["row_version"] if ot.has_row_version and "row_version" in keys else None,
    }


def _select(ot: ObjectType) -> str:
    ident(ot.source_table)
    return f"SELECT * FROM {ot.source_table}"


# ---------------------------------------------------------------------
#  单个 / 列表
# ---------------------------------------------------------------------

def get_object(conn: Conn, ot: ObjectType, pk: Any) -> dict[str, Any] | None:
    ident(ot.pk_column)
    row = conn.one(f"{_select(ot)} WHERE {ot.pk_column} = ?", (pk,))
    return row_to_object(ot, row) if row else None


def list_objects(
    conn: Conn, ot: ObjectType, q: str | None = None, limit: int = 200, offset: int = 0
) -> list[dict[str, Any]]:
    ident(ot.pk_column)
    sql = _select(ot)
    params: list[Any] = []
    if q:
        # 搜标题属性和主键就够了；真实系统这里会走搜索索引，不是 LIKE
        cols = [ot.pk_column]
        tp = ot.prop(ot.title_prop or "")
        if tp:
            cols.append(tp.source_column)
        sql += " WHERE " + " OR ".join(f"{ident(c)} LIKE ?" for c in cols)
        params += [f"%{q}%"] * len(cols)
    sql += f" ORDER BY {ot.pk_column} LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [row_to_object(ot, r) for r in conn.query(sql, params)]


def count_objects(conn: Conn, ot: ObjectType) -> int:
    ident(ot.source_table)
    row = conn.one(f"SELECT COUNT(*) AS n FROM {ot.source_table}")
    return row["n"] if row else 0


# ---------------------------------------------------------------------
#  链接遍历
# ---------------------------------------------------------------------

def traverse_sql(onto: Ontology, link: LinkType, direction: str) -> tuple[str, ObjectType]:
    """生成一条链接遍历的 SQL，并返回落地的对象类型。

    外键落在哪一端决定了要不要 JOIN：
      * 外键在 to 端，正向走  → 单表过滤
      * 外键在 to 端，反向走  → JOIN 回 from 表
      * 外键在 from 端，正向走 → JOIN 到 to 表
      * 外键在 from 端，反向走 → 单表过滤
    """
    F, T = onto.ot(link.from_type), onto.ot(link.to_type)
    if F is None or T is None:
        raise ValueError(f"链接 {link.api_name} 的两端类型不存在")
    for name in (F.source_table, T.source_table, F.pk_column, T.pk_column, link.fk_column):
        ident(name)

    fk_on_to = link.fk_table == T.source_table

    if fk_on_to and direction == "fwd":
        sql = f"SELECT t.* FROM {T.source_table} t WHERE t.{link.fk_column} = ?"
        return sql, T
    if fk_on_to and direction == "inv":
        sql = (
            f"SELECT f.* FROM {F.source_table} f "
            f"JOIN {T.source_table} t ON t.{link.fk_column} = f.{F.pk_column} "
            f"WHERE t.{T.pk_column} = ?"
        )
        return sql, F
    if direction == "fwd":
        sql = (
            f"SELECT t.* FROM {T.source_table} t "
            f"JOIN {F.source_table} f ON f.{link.fk_column} = t.{T.pk_column} "
            f"WHERE f.{F.pk_column} = ?"
        )
        return sql, T
    sql = f"SELECT f.* FROM {F.source_table} f WHERE f.{link.fk_column} = ?"
    return sql, F


def traverse(
    conn: Conn, onto: Ontology, link: LinkType, direction: str, pk: Any
) -> list[dict[str, Any]]:
    sql, target = traverse_sql(onto, link, direction)
    return [row_to_object(target, r) for r in conn.query(sql, (pk,))]


def links_of_object(
    conn: Conn, onto: Ontology, ot: ObjectType, pk: Any
) -> list[dict[str, Any]]:
    """一个对象的全部关联对象，按链接类型分组。空的分组不返回。"""
    groups = []
    for link, direction in onto.links_of(ot.api_name):
        targets = traverse(conn, onto, link, direction, pk)
        if not targets:
            continue
        sql, _ = traverse_sql(onto, link, direction)
        groups.append(
            {
                "link": link.api_name,
                "dir": direction,
                "label": link.fwd_label if direction == "fwd" else link.inv_label,
                "card": link.cardinality,
                "fk": f"{link.fk_table}.{link.fk_column}",
                "sql": " ".join(sql.split()),
                "targets": targets,
            }
        )
    return groups


# ---------------------------------------------------------------------
#  图谱
# ---------------------------------------------------------------------

def link_pairs(conn: Conn, onto: Ontology, link: LinkType, limit: int = 400) -> list[tuple[Any, Any]]:
    F, T = onto.ot(link.from_type), onto.ot(link.to_type)
    if F is None or T is None:
        return []
    ident(link.fk_column)
    if link.fk_table == T.source_table:
        sql = (
            f"SELECT t.{link.fk_column} AS a, t.{ident(T.pk_column)} AS b "
            f"FROM {ident(T.source_table)} t WHERE t.{link.fk_column} IS NOT NULL LIMIT ?"
        )
    else:
        sql = (
            f"SELECT f.{ident(F.pk_column)} AS a, f.{link.fk_column} AS b "
            f"FROM {ident(F.source_table)} f WHERE f.{link.fk_column} IS NOT NULL LIMIT ?"
        )
    return [(r["a"], r["b"]) for r in conn.query(sql, (limit,))]


def graph(conn: Conn, onto: Ontology, per_type: int = 30) -> dict[str, Any]:
    """给图谱用的节点和边。真实系统这里会有分页和邻域展开，不会一次拉全量。"""
    nodes: list[dict[str, Any]] = []
    present: set[str] = set()
    for ot in onto.object_types:
        for obj in list_objects(conn, ot, limit=per_type):
            key = f"{ot.api_name}:{obj['pk']}"
            present.add(key)
            nodes.append({"key": key, "type": ot.api_name, "pk": obj["pk"],
                          "title": obj["title"], "color": ot.color})

    edges = []
    for link in onto.link_types:
        for a, b in link_pairs(conn, onto, link):
            ka, kb = f"{link.from_type}:{a}", f"{link.to_type}:{b}"
            if ka in present and kb in present:
                edges.append({"a": ka, "b": kb, "link": link.api_name})
    return {"nodes": nodes, "edges": edges}
