"""FastAPI 应用：路由、权限、SQL 追踪、静态文件。

每个响应都带一个 `_sql` 字段，记录本次请求真正执行过的 SQL ——
界面右下角的 SQL 面板就是读它。「沿着链接走」到底变成了什么，
不用猜。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import actions as act
from . import ontology as onto_mod
from . import permissions as perm
from . import query as q
from .db import DB_PATH, Conn, SqlTrace, connect

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

app = FastAPI(title="Ontology Lab", docs_url="/api/docs", redoc_url=None)


# ---------------------------------------------------------------------
#  每请求一条连接 + 一份 SQL trace
#
#  路由全部写成同步 def：FastAPI 会把它们丢进线程池，每个请求拿到自己的
#  连接，不会出现 sqlite3 跨线程复用的问题。
# ---------------------------------------------------------------------

def get_ctx(x_actor: str | None = Header(default=None, alias="X-Actor")):
    if not DB_PATH.exists():
        raise HTTPException(503, "数据库还没建。先运行：python -m server.initdb")
    conn = connect(trace=SqlTrace())
    try:
        actor = perm.load_actor(conn, x_actor)
        yield conn, onto_mod.load(conn), actor
    finally:
        conn.raw.close()


def ok(conn: Conn, payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse({**payload, "_sql": conn.trace.dump()})


@app.exception_handler(StarletteHTTPException)
def _http_error(request: Request, exc: StarletteHTTPException):
    """把 FastAPI 默认的 {"detail": ...} 统一成 {"error", "kind"}，
    前端只需要认一种错误形状。静态资源的 404 不动。"""
    if not request.url.path.startswith("/api"):
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)
    return JSONResponse({"error": exc.detail, "kind": "http"}, status_code=exc.status_code)


@app.exception_handler(perm.Forbidden)
def _forbidden(_: Request, exc: perm.Forbidden):
    return JSONResponse({"error": str(exc), "kind": "forbidden"}, status_code=403)


@app.exception_handler(act.ValidationError)
def _invalid(_: Request, exc: act.ValidationError):
    return JSONResponse({"error": str(exc), "kind": "validation"}, status_code=422)


@app.exception_handler(act.ConflictError)
def _conflict(_: Request, exc: act.ConflictError):
    return JSONResponse({"error": str(exc), "kind": "conflict"}, status_code=409)


# ---------------------------------------------------------------------
#  本体（读）
# ---------------------------------------------------------------------

@app.get("/api/ontology")
def get_ontology(ctx=Depends(get_ctx)):
    conn, onto, actor = ctx
    payload = onto.public()
    payload["counts"] = {t.api_name: q.count_objects(conn, t) for t in onto.object_types}
    payload["actor"] = actor.public()
    payload["users"] = perm.list_users(conn)
    return ok(conn, payload)


@app.get("/api/tables")
def get_tables(ctx=Depends(get_ctx)):
    """数据库里有哪些业务表和列 —— 新建对象类型时要靠它选映射。"""
    conn, _, _ = ctx
    return ok(conn, {"tables": onto_mod.list_tables(conn)})


# ---------------------------------------------------------------------
#  对象（读）
# ---------------------------------------------------------------------

@app.get("/api/objects/{type_name}")
def list_objects(
    type_name: str,
    search: str | None = Query(default=None),
    limit: int = Query(default=200, le=1000),
    ctx=Depends(get_ctx),
):
    conn, onto, _ = ctx
    ot = onto.ot(type_name)
    if not ot:
        raise HTTPException(404, f"没有叫 {type_name} 的对象类型")
    return ok(conn, {"objects": q.list_objects(conn, ot, search, limit)})


@app.get("/api/objects/{type_name}/{pk}")
def get_object(type_name: str, pk: str, ctx=Depends(get_ctx)):
    conn, onto, _ = ctx
    ot = onto.ot(type_name)
    if not ot:
        raise HTTPException(404, f"没有叫 {type_name} 的对象类型")
    obj = q.get_object(conn, ot, pk)
    if not obj:
        raise HTTPException(404, f"{type_name} {pk} 不存在")
    return ok(conn, {"object": obj, "links": q.links_of_object(conn, onto, ot, pk)})


@app.get("/api/graph")
def get_graph(ctx=Depends(get_ctx)):
    conn, onto, _ = ctx
    return ok(conn, q.graph(conn, onto))


# ---------------------------------------------------------------------
#  操作（写）
# ---------------------------------------------------------------------

@app.get("/api/actions")
def list_actions(ctx=Depends(get_ctx)):
    conn, onto, actor = ctx
    return ok(conn, {
        "actions": [
            {
                **a.public(),
                "allowed": perm.can_run(actor, a.required_role),
                # 规则和编辑的中文描述由服务端生成，前端不必再实现一遍解释器
                "glosses": act.glosses(a, onto),
            }
            for a in onto.action_types
        ]
    })


@app.post("/api/actions/{action_id}/preview")
def preview_action(action_id: str, body: dict[str, Any] = Body(default={}), ctx=Depends(get_ctx)):
    conn, onto, actor = ctx
    action = onto.at(action_id)
    if not action:
        raise HTTPException(404, f"没有叫 {action_id} 的操作类型")
    result = act.preview(conn, onto, action, body.get("params") or {})
    result["allowed"] = perm.can_run(actor, action.required_role)
    result["requiredRole"] = action.required_role
    return ok(conn, result)


@app.post("/api/actions/{action_id}/apply")
def apply_action(action_id: str, body: dict[str, Any] = Body(default={}), ctx=Depends(get_ctx)):
    conn, onto, actor = ctx
    action = onto.at(action_id)
    if not action:
        raise HTTPException(404, f"没有叫 {action_id} 的操作类型")
    perm.require_action(actor, action_id, action.required_role)
    result = act.apply(
        conn, onto, action,
        body.get("params") or {},
        actor.username,
        body.get("versions") or {},
    )
    return ok(conn, result)


@app.get("/api/audit")
def get_audit(limit: int = Query(default=50, le=500), ctx=Depends(get_ctx)):
    conn, _, _ = ctx
    rows = conn.query("SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,))
    return ok(conn, {
        "entries": [
            {
                "id": r["id"], "ts": r["ts"], "actor": r["actor"],
                "action": r["action_type"], "status": r["status"], "error": r["error"],
                "params": json.loads(r["params"]),
                "edits": json.loads(r["edits"]),
            }
            for r in rows
        ]
    })


@app.get("/api/schema-log")
def get_schema_log(ctx=Depends(get_ctx)):
    conn, _, _ = ctx
    rows = conn.query("SELECT * FROM schema_log ORDER BY id DESC LIMIT 50")
    return ok(conn, {"entries": [dict(r) for r in rows]})


# ---------------------------------------------------------------------
#  改本体（仅管理员）
#
#  注意这里能改的是**映射和展示**，不是业务表结构。
#  想加一个真正的新字段，得先去改 schema.sql 建表 —— 这正是演练场
#  「一键加属性」掩盖掉的成本。
# ---------------------------------------------------------------------

def _note(conn: Conn, actor: perm.Actor, summary: str) -> None:
    conn.execute(
        "INSERT INTO schema_log (ts, actor, summary) VALUES (?, ?, ?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), actor.username, summary),
    )


@app.patch("/api/ontology/object-types/{type_name}")
def patch_object_type(type_name: str, body: dict[str, Any] = Body(...), ctx=Depends(get_ctx)):
    conn, onto, actor = ctx
    perm.require_admin(actor)
    ot = onto.ot(type_name)
    if not ot:
        raise HTTPException(404, f"没有叫 {type_name} 的对象类型")
    allowed = {"display_name": "display_name", "title_prop": "title_prop", "color": "color"}
    sets, params, changed = [], [], []
    for key, col in allowed.items():
        camel = key if key in body else _camel(key)
        if camel in body:
            sets.append(f"{col} = ?")
            params.append(body[camel])
            changed.append(f"{col}={body[camel]}")
    if not sets:
        raise HTTPException(400, "没有可改的字段")
    params.append(type_name)
    conn.execute(f"UPDATE object_type SET {', '.join(sets)} WHERE api_name = ?", params)
    _note(conn, actor, f"对象类型 {type_name}：{', '.join(changed)}")
    return ok(conn, {"updated": type_name})


@app.patch("/api/ontology/properties/{type_name}/{prop_name}")
def patch_property(type_name: str, prop_name: str, body: dict[str, Any] = Body(...), ctx=Depends(get_ctx)):
    conn, onto, actor = ctx
    perm.require_admin(actor)
    ot = onto.ot(type_name)
    if not ot or not ot.prop(prop_name):
        raise HTTPException(404, "属性不存在")
    sets, params, changed = [], [], []
    for camel, col in (("displayName", "display_name"), ("dataType", "data_type")):
        if camel in body:
            sets.append(f"{col} = ?")
            params.append(body[camel])
            changed.append(f"{col}={body[camel]}")
    if not sets:
        raise HTTPException(400, "没有可改的字段")
    params += [type_name, prop_name]
    conn.execute(
        f"UPDATE object_property SET {', '.join(sets)} WHERE object_type = ? AND api_name = ?",
        params,
    )
    _note(conn, actor, f"属性 {type_name}.{prop_name}：{', '.join(changed)}")
    return ok(conn, {"updated": f"{type_name}.{prop_name}"})


@app.post("/api/ontology/properties/{type_name}")
def add_property(type_name: str, body: dict[str, Any] = Body(...), ctx=Depends(get_ctx)):
    """新增属性 = 映射一个**已存在的列**。列不存在就得先改表 —— 这是真实成本。"""
    conn, onto, actor = ctx
    perm.require_admin(actor)
    ot = onto.ot(type_name)
    if not ot:
        raise HTTPException(404, f"没有叫 {type_name} 的对象类型")

    api_name = (body.get("apiName") or "").strip()
    column = (body.get("sourceColumn") or "").strip()
    if not api_name or not column:
        raise HTTPException(400, "apiName 和 sourceColumn 都不能空")
    if ot.prop(api_name):
        raise HTTPException(409, f"{type_name} 已经有一个叫 {api_name} 的属性了")

    cols = {c["name"] for c in conn.query(f"PRAGMA table_info({q.ident(ot.source_table)})")}
    if column not in cols:
        raise HTTPException(
            422,
            f"表 {ot.source_table} 上没有 {column} 这一列。"
            f"本体只能映射已存在的列 —— 想要新字段，得先改 schema.sql 建表。",
        )

    conn.execute(
        "INSERT INTO object_property "
        "(object_type, api_name, display_name, data_type, source_column, scale, sort_order) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            type_name, api_name,
            body.get("displayName") or api_name,
            body.get("dataType") or "string",
            column,
            body.get("scale"),
            len(ot.props) + 1,
        ),
    )
    _note(conn, actor, f"{type_name} 新增属性 {api_name} ← 列 {column}")
    return ok(conn, {"added": f"{type_name}.{api_name}"})


@app.delete("/api/ontology/properties/{type_name}/{prop_name}")
def delete_property(type_name: str, prop_name: str, ctx=Depends(get_ctx)):
    """解除映射。业务表和数据一动不动 —— 和演练场里"删属性=删数据"完全不同。"""
    conn, onto, actor = ctx
    perm.require_admin(actor)
    conn.execute(
        "DELETE FROM object_property WHERE object_type = ? AND api_name = ?",
        (type_name, prop_name),
    )
    _note(conn, actor, f"{type_name} 解除属性映射 {prop_name}（业务表未改动）")
    return ok(conn, {"removed": f"{type_name}.{prop_name}"})


@app.post("/api/ontology/link-types")
def add_link_type(body: dict[str, Any] = Body(...), ctx=Depends(get_ctx)):
    """新建链接类型 = 挑一个**真实存在的外键**。

    界面上那个下拉框列的是 PRAGMA foreign_key_list 查出来的真外键 ——
    「外键变成链接」这句话在这里落到实处。
    """
    conn, onto, actor = ctx
    perm.require_admin(actor)

    api_name = (body.get("apiName") or "").strip()
    from_type, to_type = body.get("fromType"), body.get("toType")
    fk_table, fk_column = body.get("fkTable"), body.get("fkColumn")
    if not all([api_name, from_type, to_type, fk_table, fk_column]):
        raise HTTPException(400, "apiName / fromType / toType / fkTable / fkColumn 都不能空")
    if onto.lt(api_name):
        raise HTTPException(409, f"已经有一个叫 {api_name} 的链接类型了")
    F, T = onto.ot(from_type), onto.ot(to_type)
    if not F or not T:
        raise HTTPException(404, "两端的对象类型必须都存在")
    if fk_table not in (F.source_table, T.source_table):
        raise HTTPException(
            422, f"外键所在的表 {fk_table} 必须是两端之一（{F.source_table} 或 {T.source_table}）"
        )
    real_fks = {
        (r["from"], r["table"])
        for r in conn.query(f"PRAGMA foreign_key_list({q.ident(fk_table)})")
    }
    if not any(col == fk_column for col, _ in real_fks):
        raise HTTPException(
            422,
            f"{fk_table}.{fk_column} 不是一个外键。链接必须由真实存在的外键实现 —— "
            f"该表上的外键有：{sorted(c for c, _ in real_fks) or '（没有）'}",
        )

    conn.execute(
        "INSERT INTO link_type (api_name, from_type, to_type, cardinality, fwd_label, inv_label, "
        "join_kind, fk_table, fk_column, sort_order) VALUES (?, ?, ?, ?, ?, ?, 'fk', ?, ?, ?)",
        (api_name, from_type, to_type, body.get("cardinality") or "1 : N",
         body.get("fwdLabel") or "关联对象", body.get("invLabel") or "来源对象",
         fk_table, fk_column, len(onto.link_types) + 1),
    )
    _note(conn, actor, f"新建链接类型 {api_name}：{from_type}→{to_type}，由 {fk_table}.{fk_column} 实现")
    return ok(conn, {"added": api_name})


@app.delete("/api/ontology/link-types/{link_id}")
def delete_link_type(link_id: str, ctx=Depends(get_ctx)):
    """解除链接映射。外键列和数据都不动。"""
    conn, _, actor = ctx
    perm.require_admin(actor)
    conn.execute("DELETE FROM link_type WHERE api_name = ?", (link_id,))
    _note(conn, actor, f"解除链接映射 {link_id}（外键列未改动）")
    return ok(conn, {"removed": link_id})


@app.put("/api/ontology/action-types/{action_id}")
def put_action_type(action_id: str, body: dict[str, Any] = Body(...), ctx=Depends(get_ctx)):
    conn, onto, actor = ctx
    perm.require_admin(actor)
    exists = onto.at(action_id) is not None
    payload = (
        body.get("displayName") or action_id,
        body.get("description") or "",
        json.dumps(body.get("params") or [], ensure_ascii=False),
        json.dumps(body.get("rules") or [], ensure_ascii=False),
        json.dumps(body.get("edits") or [], ensure_ascii=False),
        body.get("requiredRole"),
    )
    if exists:
        conn.execute(
            "UPDATE action_type SET display_name=?, description=?, params=?, rules=?, "
            "edits=?, required_role=? WHERE api_name=?",
            (*payload, action_id),
        )
    else:
        conn.execute(
            "INSERT INTO action_type "
            "(display_name, description, params, rules, edits, required_role, api_name, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*payload, action_id, len(onto.action_types) + 1),
        )
    _note(conn, actor, f"{'更新' if exists else '新建'}操作类型 {action_id}")
    return ok(conn, {"saved": action_id})


@app.delete("/api/ontology/action-types/{action_id}")
def delete_action_type(action_id: str, ctx=Depends(get_ctx)):
    conn, _, actor = ctx
    perm.require_admin(actor)
    conn.execute("DELETE FROM action_type WHERE api_name = ?", (action_id,))
    _note(conn, actor, f"删除操作类型 {action_id}")
    return ok(conn, {"removed": action_id})


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(w.capitalize() for w in rest)


# ---------------------------------------------------------------------
#  静态前端（放最后，别把 /api/* 吃掉）
# ---------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
