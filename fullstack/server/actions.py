"""Action 引擎 —— 本体唯一的写路径。

声明式的 Target / Ref / Rule / EditSpec 在这里被求值，然后落成 SQL。
和内存版最大的三个区别：

  1. 编辑跑在**一个事务**里，任何一步失败整体回滚
  2. 带**乐观锁**：客户端提交它读到的 row_version，对不上就 409
  3. 「建立链接」落地就是**写外键列** —— 所以基数由表结构天然约束
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .db import Conn
from .ontology import ActionType, Ontology, Property
from .query import get_object, ident, traverse


class ValidationError(Exception):
    """校验没过，或者参数不完整。→ 422"""


class ConflictError(Exception):
    """乐观锁撞车：别人在你读之后改过这条记录。→ 409"""


# ---------------------------------------------------------------------
#  求值上下文
# ---------------------------------------------------------------------

class Ctx:
    def __init__(self, conn: Conn, onto: Ontology, params: dict[str, Any]) -> None:
        self.conn = conn
        self.onto = onto
        self.params = params
        self.created: dict[str, tuple[str, Any]] = {}   # edit uid → (类型, 主键)
        self._cache: dict[tuple[str, Any], dict[str, Any] | None] = {}

    def load(self, type_name: str, pk: Any) -> dict[str, Any] | None:
        key = (type_name, pk)
        if key not in self._cache:
            ot = self.onto.ot(type_name)
            self._cache[key] = get_object(self.conn, ot, pk) if ot else None
        return self._cache[key]

    def invalidate(self, type_name: str, pk: Any) -> None:
        self._cache.pop((type_name, pk), None)


# ---------------------------------------------------------------------
#  Target：解析成 (类型, 主键)
# ---------------------------------------------------------------------

def resolve_target(t: dict[str, Any] | None, ctx: Ctx, action: ActionType) -> tuple[str, Any] | None:
    if not t:
        return None
    kind = t.get("kind")
    if kind == "param":
        spec = next((p for p in action.params if p["id"] == t["param"]), None)
        pk = ctx.params.get(t["param"])
        if spec is None or pk in (None, ""):
            return None
        return (spec.get("objectType"), pk)
    if kind == "new":
        return ctx.created.get(t["uid"])
    if kind == "hop":
        base = resolve_target(t.get("from"), ctx, action)
        if not base:
            return None
        link = ctx.onto.lt(t["link"])
        if not link:
            return None
        hits = traverse(ctx.conn, ctx.onto, link, t.get("dir", "fwd"), base[1])
        if not hits:
            return None
        return (hits[0]["type"], hits[0]["pk"])
    return None


def target_type(t: dict[str, Any] | None, ctx_onto: Ontology, action: ActionType) -> str | None:
    """不查库，只按元数据推导目标的类型（描述文字和属性下拉要用）。"""
    if not t:
        return None
    kind = t.get("kind")
    if kind == "param":
        spec = next((p for p in action.params if p["id"] == t["param"]), None)
        return spec.get("objectType") if spec else None
    if kind == "new":
        e = next((e for e in action.edits if e.get("uid") == t.get("uid")), None)
        return e.get("objectType") if e else None
    if kind == "hop":
        link = ctx_onto.lt(t.get("link", ""))
        if not link:
            return None
        return link.to_type if t.get("dir", "fwd") == "fwd" else link.from_type
    return None


# ---------------------------------------------------------------------
#  Ref：解析成标量
# ---------------------------------------------------------------------

_MISSING = object()


def resolve_ref(r: dict[str, Any] | None, ctx: Ctx, action: ActionType) -> Any:
    if not r:
        return _MISSING
    src = r.get("src")
    if src == "const":
        return r.get("value")
    if src == "consts":
        return r.get("values", [])
    if src == "param":
        return ctx.params.get(r["param"], _MISSING)
    if src == "prop":
        tgt = resolve_target(r.get("of"), ctx, action)
        if not tgt:
            return _MISSING
        obj = ctx.load(*tgt)
        if not obj:
            return _MISSING
        return obj["props"].get(r["prop"], _MISSING)
    if src == "calc":
        a, b = resolve_ref(r.get("a"), ctx, action), resolve_ref(r.get("b"), ctx, action)
        if a is _MISSING or b is _MISSING:
            return _MISSING
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            return _MISSING
        op = r.get("op", "+")
        return a * b if op == "*" else a - b if op == "-" else a + b
    if src == "now":
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    if src == "today":
        return (datetime.now() + timedelta(days=int(r.get("plusDays") or 0))).strftime("%Y-%m-%d")
    if src == "newId":
        return r.get("_pk", _MISSING)
    return _MISSING


# ---------------------------------------------------------------------
#  规则
# ---------------------------------------------------------------------

def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _compare(op: str, a: Any, b: Any) -> bool:
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == "in":
        return isinstance(b, list) and a in b
    na, nb = _num(a), _num(b)
    if na is None or nb is None:
        return False
    return {">": na > nb, ">=": na >= nb, "<": na < nb, "<=": na <= nb}.get(op, False)


OP_CN = {"==": "等于", "!=": "不等于", ">": "大于", ">=": "大于等于",
         "<": "小于", "<=": "小于等于", "in": "属于"}


def describe_ref(r: dict[str, Any] | None, onto: Ontology, action: ActionType) -> str:
    if not r:
        return "？"
    src = r.get("src")
    if src == "const":
        return str(r.get("value"))
    if src == "consts":
        return " / ".join(str(v) for v in r.get("values", []))
    if src == "param":
        spec = next((p for p in action.params if p["id"] == r["param"]), None)
        return spec["cn"] if spec else r["param"]
    if src == "prop":
        ty = target_type(r.get("of"), onto, action)
        ot = onto.ot(ty) if ty else None
        p = ot.prop(r["prop"]) if ot else None
        return f"{describe_target(r.get('of'), onto, action)}.{p.display_name if p else r['prop']}"
    if src == "calc":
        return f"{describe_ref(r.get('a'), onto, action)} {r.get('op')} {describe_ref(r.get('b'), onto, action)}"
    return {"now": "当前时间", "today": "今天", "newId": "新对象的主键"}.get(src, "？")


def describe_target(t: dict[str, Any] | None, onto: Ontology, action: ActionType) -> str:
    if not t:
        return "？"
    kind = t.get("kind")
    if kind == "param":
        spec = next((p for p in action.params if p["id"] == t["param"]), None)
        return spec["cn"] if spec else t["param"]
    if kind == "new":
        ty = target_type(t, onto, action)
        ot = onto.ot(ty) if ty else None
        return f"新建的{ot.display_name}" if ot else "新建对象"
    if kind == "hop":
        link = onto.lt(t.get("link", ""))
        label = (link.fwd_label if t.get("dir") == "fwd" else link.inv_label) if link else t.get("link")
        return f"{describe_target(t.get('from'), onto, action)} 的 {label}"
    return "？"


def describe_rule(rule: dict[str, Any], onto: Ontology, action: ActionType) -> str:
    kind = rule.get("type")
    if kind == "compare":
        return (f"{describe_ref(rule.get('left'), onto, action)} "
                f"{OP_CN.get(rule.get('op'), rule.get('op'))} "
                f"{describe_ref(rule.get('right'), onto, action)}")
    if kind == "linkCount":
        link = onto.lt(rule.get("link", ""))
        label = (link.fwd_label if rule.get("dir") == "fwd" else link.inv_label) if link else rule.get("link")
        return (f"{describe_target(rule.get('target'), onto, action)} 的「{label}」数量 "
                f"{OP_CN.get(rule.get('op'), rule.get('op'))} {rule.get('n')}")
    if kind == "linkExists":
        return (f"{describe_target(rule.get('a'), onto, action)} 与 "
                f"{describe_target(rule.get('b'), onto, action)} 之间"
                f"{'存在' if rule.get('expect') else '不存在'}「{rule.get('link')}」链接")
    return ""


MODE_CN = {"set": "设为", "inc": "增加", "dec": "减少", "mul": "乘以"}


def describe_spec(spec: dict[str, Any], onto: Ontology, action: ActionType) -> dict[str, str]:
    """把一条声明式编辑翻成人话。前端直接显示，不用再实现一遍。"""
    op = spec.get("op")
    if op == "create":
        ot = onto.ot(spec.get("objectType", ""))
        return {"op": "CREATE", "txt": f"新建一个{ot.display_name if ot else spec.get('objectType')}对象"}
    if op == "modify":
        ty = target_type(spec.get("target"), onto, action)
        ot = onto.ot(ty) if ty else None
        p = ot.prop(spec.get("prop", "")) if ot else None
        return {"op": "MODIFY", "txt": (
            f"{describe_target(spec.get('target'), onto, action)}."
            f"{p.display_name if p else spec.get('prop')} "
            f"{MODE_CN.get(spec.get('mode'), spec.get('mode'))} "
            f"{describe_ref(spec.get('value'), onto, action)}")}
    if op in ("link", "unlink"):
        link = onto.lt(spec.get("link", ""))
        fk = f"（写 {link.fk_table}.{link.fk_column}）" if link else ""
        arrow = f"──{spec.get('link')}──▸"
        txt = (f"{describe_target(spec.get('a'), onto, action)} {arrow} "
               f"{describe_target(spec.get('b'), onto, action)}{fk}")
        return {"op": op.upper(), "txt": ("断开 " + txt) if op == "unlink" else txt}
    return {"op": "DELETE", "txt": f"删除 {describe_target(spec.get('target'), onto, action)}"}


def glosses(action: ActionType, onto: Ontology) -> dict[str, Any]:
    return {
        "rules": [describe_rule(r, onto, action) for r in action.rules],
        "edits": [describe_spec(e, onto, action) for e in action.edits],
    }


def eval_rule(rule: dict[str, Any], ctx: Ctx, action: ActionType) -> tuple[bool, str]:
    kind = rule.get("type")
    if kind == "compare":
        left = resolve_ref(rule.get("left"), ctx, action)
        right = resolve_ref(rule.get("right"), ctx, action)
        if left is _MISSING:
            return False, "取不到左值"
        ok = _compare(rule.get("op", "=="), left, right)
        return ok, "" if ok else f"当前 {left}"
    if kind == "linkCount":
        tgt = resolve_target(rule.get("target"), ctx, action)
        if not tgt:
            return False, "取不到目标对象"
        link = ctx.onto.lt(rule.get("link", ""))
        if not link:
            return False, "链接类型不存在"
        n = len(traverse(ctx.conn, ctx.onto, link, rule.get("dir", "fwd"), tgt[1]))
        ok = _compare(rule.get("op", "=="), n, rule.get("n"))
        return ok, "" if ok else f"当前 {n} 条"
    if kind == "linkExists":
        a = resolve_target(rule.get("a"), ctx, action)
        b = resolve_target(rule.get("b"), ctx, action)
        if not a or not b:
            return False, "取不到两端对象"
        link = ctx.onto.lt(rule.get("link", ""))
        if not link:
            return False, "链接类型不存在"
        hits = traverse(ctx.conn, ctx.onto, link, "fwd", a[1])
        has = any(h["pk"] == b[1] for h in hits)
        ok = has if rule.get("expect") else not has
        return ok, "" if ok else ("链接已存在" if has else "链接不存在")
    return True, ""


# ---------------------------------------------------------------------
#  编辑：生成具体化的写入指令（还没有真的写）
# ---------------------------------------------------------------------

def _peek_next_pk(conn: Conn, onto: Ontology, type_name: str, offset: int) -> str:
    ot = onto.ot(type_name)
    row = conn.one("SELECT id_prefix, next_seq FROM object_type WHERE api_name = ?", (type_name,))
    prefix = (row["id_prefix"] if row else None) or (ot.api_name[:1].upper() if ot else "X")
    seq = (row["next_seq"] if row else 1) + offset
    return f"{prefix}-{seq}"


def build_edits(ctx: Ctx, action: ActionType) -> list[dict[str, Any]]:
    """把声明式 edits 具体化成可执行的写入指令。纯读，不改任何东西。"""
    onto = ctx.onto
    out: list[dict[str, Any]] = []

    # 先给所有 create 分配主键，后面的 link 才引用得到
    seq_used: dict[str, int] = {}
    for spec in action.edits:
        if spec.get("op") != "create":
            continue
        ty = spec.get("objectType")
        ot = onto.ot(ty)
        if not ot:
            continue
        if spec.get("pk"):
            pk = resolve_ref(spec["pk"], ctx, action)
            if pk is _MISSING or pk in (None, ""):
                raise ValidationError(f"{ot.display_name} 的主键没给")
        else:
            offset = seq_used.get(ty, 0)
            pk = _peek_next_pk(ctx.conn, onto, ty, offset)
            seq_used[ty] = offset + 1
        ctx.created[spec["uid"]] = (ty, pk)

    for spec in action.edits:
        op = spec.get("op")

        if op == "create":
            ty, pk = ctx.created[spec["uid"]]
            ot = onto.ot(ty)
            cols: dict[str, Any] = {ot.pk_column: pk}
            shown: dict[str, Any] = {}
            for prop in ot.props:
                ref = (spec.get("props") or {}).get(prop.api_name)
                if not ref:
                    continue
                if ref.get("src") == "newId":
                    val: Any = pk
                else:
                    val = resolve_ref(ref, ctx, action)
                if val is _MISSING:
                    continue
                cols[prop.source_column] = prop.to_storage(val)
                shown[prop.api_name] = val
            out.append({
                "op": "create", "type": ty, "pk": pk, "table": ot.source_table,
                "columns": cols, "shown": shown,
                "desc": f"新建 {ot.display_name} {pk}",
            })

        elif op == "modify":
            tgt = resolve_target(spec.get("target"), ctx, action)
            if not tgt:
                continue
            ty, pk = tgt
            ot = onto.ot(ty)
            prop = ot.prop(spec.get("prop", "")) if ot else None
            if not ot or not prop:
                continue
            obj = ctx.load(ty, pk)
            if not obj:
                continue
            value = resolve_ref(spec.get("value"), ctx, action)
            if value is _MISSING:
                continue
            mode = spec.get("mode", "set")
            if mode == "set":
                new_display = value
            else:
                cur, delta = _num(obj["props"].get(prop.api_name)), _num(value)
                if cur is None or delta is None:
                    continue
                new_display = {"inc": cur + delta, "dec": cur - delta, "mul": cur * delta}[mode]
                if prop.data_type == "integer":
                    new_display = int(new_display)
            out.append({
                "op": "modify", "type": ty, "pk": pk, "table": ot.source_table,
                "columns": {prop.source_column: prop.to_storage(new_display)},
                "shown": {prop.api_name: new_display},
                "desc": f"{obj['title']} 的{prop.display_name} → {new_display}",
            })
            # 同一个 Action 里后面的编辑要看到这次改动
            obj["props"][prop.api_name] = new_display

        elif op in ("link", "unlink"):
            a = resolve_target(spec.get("a"), ctx, action)
            b = resolve_target(spec.get("b"), ctx, action)
            link = onto.lt(spec.get("link", ""))
            if not a or not b or not link:
                continue
            F, T = onto.ot(link.from_type), onto.ot(link.to_type)
            # 外键在哪一端，决定这条链接落到哪张表的哪一行
            if link.fk_table == T.source_table:
                table, row_pk_col, row_pk = T.source_table, T.pk_column, b[1]
                value = a[1] if op == "link" else None
            else:
                table, row_pk_col, row_pk = F.source_table, F.pk_column, a[1]
                value = b[1] if op == "link" else None
            a_title = (ctx.load(*a) or {}).get("title", a[1])
            b_title = (ctx.load(*b) or {}).get("title", b[1])
            out.append({
                "op": op, "link": link.api_name, "table": table,
                "pkColumn": row_pk_col, "pk": row_pk,
                "fkColumn": link.fk_column, "value": value,
                "type": T.api_name if link.fk_table == T.source_table else F.api_name,
                "desc": (f"{a_title} ──{link.api_name}──▸ {b_title}" if op == "link"
                         else f"断开 {a_title} ──{link.api_name}──▸ {b_title}"),
            })

        elif op == "delete":
            tgt = resolve_target(spec.get("target"), ctx, action)
            if not tgt:
                continue
            ty, pk = tgt
            ot = onto.ot(ty)
            obj = ctx.load(ty, pk)
            if not ot or not obj:
                continue
            out.append({
                "op": "delete", "type": ty, "pk": pk, "table": ot.source_table,
                "desc": f"删除 {obj['title']}",
            })

    return out


# ---------------------------------------------------------------------
#  预览（只读）
# ---------------------------------------------------------------------

def preview(conn: Conn, onto: Ontology, action: ActionType, params: dict[str, Any]) -> dict[str, Any]:
    ctx = Ctx(conn, onto, params)
    complete = all(
        params.get(p["id"]) not in (None, "") for p in action.params
    )

    results = []
    for rule in action.rules:
        text = describe_rule(rule, onto, action)
        if not complete:
            results.append({"cn": text, "state": "idle", "why": ""})
            continue
        try:
            ok, why = eval_rule(rule, ctx, action)
        except Exception as exc:  # noqa: BLE001
            ok, why = False, f"求值失败：{exc}"
        results.append({"cn": text, "state": "pass" if ok else "fail", "why": why})

    valid = complete and all(r["state"] == "pass" for r in results)
    edits: list[dict[str, Any]] = []
    if valid:
        try:
            edits = build_edits(Ctx(conn, onto, params), action)
        except ValidationError as exc:
            valid = False
            results.append({"cn": str(exc), "state": "fail", "why": ""})

    return {"complete": complete, "rules": results, "valid": valid, "edits": edits}


# ---------------------------------------------------------------------
#  应用（事务 + 乐观锁）
# ---------------------------------------------------------------------

def apply(
    conn: Conn,
    onto: Ontology,
    action: ActionType,
    params: dict[str, Any],
    actor: str,
    versions: dict[str, int] | None = None,
) -> dict[str, Any]:
    versions = versions or {}
    edits: list[dict[str, Any]] = []
    try:
        # 校验和主键分配都放进事务里做。BEGIN IMMEDIATE 拿到写锁，
        # 别人没法在"校验通过"和"真正写入"之间插一脚。
        with conn.transaction():
            pre = preview(conn, onto, action, params)
            if not pre["valid"]:
                failed = next((r["cn"] for r in pre["rules"] if r["state"] == "fail"), "参数不完整")
                raise ValidationError(f"校验未通过：{failed}")
            edits = pre["edits"]

            # 一个 Action 可能对同一个对象改好几次（比如发货要写承运商又写状态）。
            # 第一次比对成功后 row_version 就自增了，后面几次不能再拿旧版本去比 ——
            # 那会变成自己撞自己。校验过一次就够：整个事务持有写锁，别人插不进来。
            pending = dict(versions)
            for e in edits:
                _execute(conn, onto, e, pending)
                if e["op"] in ("modify", "delete"):
                    pending.pop(f"{e['type']}:{e['pk']}", None)

            # 同一个 Action 建了几个同类型对象，序号就推进几格
            created_per_type: dict[str, int] = {}
            for e in edits:
                if e["op"] == "create":
                    created_per_type[e["type"]] = created_per_type.get(e["type"], 0) + 1
            for ty, n in created_per_type.items():
                ot = onto.ot(ty)
                if ot and ot.id_prefix:
                    conn.execute(
                        "UPDATE object_type SET next_seq = next_seq + ? WHERE api_name = ?",
                        (n, ty),
                    )

            _log(conn, actor, action.api_name, params, edits, "applied", None)

    except (ValidationError, ConflictError) as exc:
        # 事务已经回滚，日志要在事务外补写 —— 失败也是必须留痕的事实
        _log(conn, actor, action.api_name, params, [], "rejected", str(exc))
        raise

    return {"edits": edits}


def _execute(conn: Conn, onto: Ontology, e: dict[str, Any], versions: dict[str, int]) -> None:
    op = e["op"]
    table = ident(e["table"])

    if op == "create":
        cols = [ident(c) for c in e["columns"]]
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)})")
        conn.execute(sql, list(e["columns"].values()))
        return

    if op == "modify":
        ot = onto.ot(e["type"])
        sets = [f"{ident(c)} = ?" for c in e["columns"]]
        values = list(e["columns"].values())
        where = f"{ident(ot.pk_column)} = ?"
        params = values + [e["pk"]]
        if ot.has_row_version:
            sets.append("row_version = row_version + 1")
            expected = versions.get(f"{e['type']}:{e['pk']}")
            if expected is not None:
                where += " AND row_version = ?"
                params = values + [e["pk"], expected]
        n = conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE {where}", params)
        if n == 0:
            raise ConflictError(
                f"{e['type']} {e['pk']} 已被他人修改（你读到的版本已过期），本次提交整体回滚"
            )
        return

    if op in ("link", "unlink"):
        # 建立链接 = 写外键列。所以基数由表结构守着：
        # orders.buyer_id 只能装一个值，一张订单就只能有一个买家。
        sql = (f"UPDATE {table} SET {ident(e['fkColumn'])} = ? "
               f"WHERE {ident(e['pkColumn'])} = ?")
        n = conn.execute(sql, (e["value"], e["pk"]))
        if n == 0:
            raise ConflictError(f"{e['table']} 里找不到 {e['pk']}，无法{'建立' if op == 'link' else '断开'}链接")
        return

    if op == "delete":
        ot = onto.ot(e["type"])
        where = f"{ident(ot.pk_column)} = ?"
        params: list[Any] = [e["pk"]]
        if ot.has_row_version:
            expected = versions.get(f"{e['type']}:{e['pk']}")
            if expected is not None:
                where += " AND row_version = ?"
                params.append(expected)
        n = conn.execute(f"DELETE FROM {table} WHERE {where}", params)
        if n == 0:
            raise ConflictError(f"{e['type']} {e['pk']} 已被他人修改或删除，本次提交整体回滚")
        return


def _log(
    conn: Conn, actor: str, action_id: str, params: dict[str, Any],
    edits: list[dict[str, Any]], status: str, error: str | None,
) -> None:
    import json

    conn.execute(
        "INSERT INTO action_log (ts, actor, action_type, params, edits, status, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            actor, action_id,
            json.dumps(params, ensure_ascii=False),
            json.dumps([{k: v for k, v in e.items() if k != "columns"} for e in edits],
                       ensure_ascii=False),
            status, error,
        ),
    )
