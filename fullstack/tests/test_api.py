"""API 层测试：端点、权限、并发、SQL 追踪。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import db as db_mod  # noqa: E402
from server.initdb import init  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    init(path, quiet=True)
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    from server import main
    monkeypatch.setattr(main, "DB_PATH", path)
    with TestClient(main.app) as c:
        yield c


def as_(client, user):
    client.headers.update({"X-Actor": user})
    return client


# --- 读端点 ---------------------------------------------------------

def test_ontology_exposes_mapping(client):
    r = client.get("/api/ontology")
    assert r.status_code == 200
    data = r.json()
    order = next(t for t in data["objectTypes"] if t["id"] == "Order")
    assert order["sourceTable"] == "orders"
    assert order["pkColumn"] == "order_no"
    status = next(p for p in order["props"] if p["id"] == "status")
    assert status["sourceColumn"] == "ord_status"
    placed = next(l for l in data["linkTypes"] if l["id"] == "placed")
    assert placed["fkTable"] == "orders" and placed["fkColumn"] == "buyer_id"


def test_every_response_carries_sql_trace(client):
    data = client.get("/api/objects/Order/O-1001").json()
    assert data["_sql"], "响应里应该带 SQL 追踪"
    joined = " ".join(e["sql"] for e in data["_sql"])
    assert "JOIN" in joined.upper(), "查关联对象应该产生 JOIN"


def test_object_detail_has_links_both_ways(client):
    data = client.get("/api/objects/Order/O-1001").json()
    pairs = {(g["link"], g["dir"]) for g in data["links"]}
    assert ("placed", "inv") in pairs
    assert ("hasLine", "fwd") in pairs


def test_tables_endpoint_lists_business_tables_only(client):
    names = {t["name"] for t in client.get("/api/tables").json()["tables"]}
    assert {"buyer", "product", "orders", "order_line"} <= names
    assert "object_type" not in names and "action_log" not in names


def test_graph_edges_reference_nodes(client):
    g = client.get("/api/graph").json()
    keys = {n["key"] for n in g["nodes"]}
    assert g["edges"]
    assert all(e["a"] in keys and e["b"] in keys for e in g["edges"])


# --- 权限 -----------------------------------------------------------

def test_actions_are_flagged_by_role(client):
    acts = {a["id"]: a["allowed"] for a in as_(client, "xiaozhang").get("/api/actions").json()["actions"]}
    assert acts["createOrder"] is True       # 客服
    assert acts["shipOrder"] is False        # 仓管才行
    assert acts["createProduct"] is False    # 管理员才行


def test_admin_can_run_everything(client):
    acts = {a["id"]: a["allowed"] for a in as_(client, "admin").get("/api/actions").json()["actions"]}
    assert all(acts.values())


def test_wrong_role_gets_403(client):
    as_(client, "xiaozhang")
    client.post("/api/actions/createOrder/apply",
                json={"params": {"buyer": "B-1", "shipTo": "上海"}})
    client.post("/api/actions/addLine/apply",
                json={"params": {"order": "O-1003", "product": "HUB-C8", "qty": 1}})
    client.post("/api/actions/payOrder/apply", json={"params": {"order": "O-1003"}})

    r = client.post("/api/actions/shipOrder/apply",
                    json={"params": {"order": "O-1003", "carrier": "顺丰"}})
    assert r.status_code == 403
    assert r.json()["kind"] == "forbidden"
    assert "仓管" in r.json()["error"]

    # 换成仓管就通过
    r = as_(client, "laowang").post("/api/actions/shipOrder/apply",
                                    json={"params": {"order": "O-1003", "carrier": "顺丰"}})
    assert r.status_code == 200


def test_non_admin_cannot_edit_ontology(client):
    r = as_(client, "laowang").patch("/api/ontology/object-types/Order",
                                     json={"displayName": "订单单"})
    assert r.status_code == 403


# --- 校验与冲突 -----------------------------------------------------

def test_validation_failure_is_422(client):
    r = as_(client, "admin").post("/api/actions/addLine/apply",
                                  json={"params": {"order": "O-1001", "product": "HUB-C8", "qty": 1}})
    assert r.status_code == 422
    assert r.json()["kind"] == "validation"


def test_stale_version_is_409(client):
    as_(client, "admin")
    order = client.get("/api/objects/Order/O-1002").json()["object"]
    stale = {"Order:O-1002": order["rowVersion"]}

    r1 = client.post("/api/actions/payOrder/apply",
                     json={"params": {"order": "O-1002"}, "versions": stale})
    assert r1.status_code == 200

    r2 = client.post("/api/actions/cancelOrder/apply",
                     json={"params": {"order": "O-1002", "reason": "缺货"}, "versions": stale})
    assert r2.status_code == 409
    assert r2.json()["kind"] == "conflict"


def _race(client, action_id, body, actor, n=2):
    """让 n 个线程卡在同一个起跑线上，同时打同一个 Action。"""
    results: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def fire():
        barrier.wait()
        r = client.post(f"/api/actions/{action_id}/apply", json=body,
                        headers={"X-Actor": actor})
        with lock:
            results.append(r.status_code)

    threads = [threading.Thread(target=fire) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sorted(results)


def test_concurrent_shipment_second_is_revalidated_out(client):
    """两个人同时发货：一个成功，另一个被**事务内重新校验**拦下。

    输的那个拿到的是 422 而不是 409 —— 因为等它拿到写锁时订单已经是「已发货」，
    规则本身就不满足了。这比一句"版本冲突"更准确。
    """
    as_(client, "admin")
    client.post("/api/actions/createOrder/apply",
                json={"params": {"buyer": "B-1", "shipTo": "上海"}})
    client.post("/api/actions/addLine/apply",
                json={"params": {"order": "O-1003", "product": "HUB-C8", "qty": 1}})
    client.post("/api/actions/payOrder/apply", json={"params": {"order": "O-1003"}})

    version = client.get("/api/objects/Order/O-1003").json()["object"]["rowVersion"]
    results = _race(client, "shipOrder",
                    {"params": {"order": "O-1003", "carrier": "顺丰"},
                     "versions": {"Order:O-1003": version}}, "laowang")

    assert results == [200, 422], f"期望一成一败，实际 {results}"
    assert client.get("/api/objects/Order/O-1003").json()["object"]["props"]["status"] == "已发货"


def test_concurrent_restock_hits_optimistic_lock(client):
    """两个人同时补货：这才是乐观锁真正要防的场景 —— 丢失更新。

    补货的规则（数量 ≥ 1）在两边都成立，重新校验拦不住它。
    如果没有版本检查，两次 +10 都基于同一个旧库存算，结果只涨了 10，
    另一个人的补货就这么静默消失了。版本检查把它变成一次明确的 409。
    """
    as_(client, "admin")
    before = client.get("/api/objects/Product/HUB-C8").json()["object"]
    stock0, version = before["props"]["stock"], before["rowVersion"]

    results = _race(client, "restock",
                    {"params": {"product": "HUB-C8", "qty": 10},
                     "versions": {"Product:HUB-C8": version}}, "laowang")

    assert results == [200, 409], f"期望一成一败，实际 {results}"
    after = client.get("/api/objects/Product/HUB-C8").json()["object"]
    assert after["props"]["stock"] == stock0 + 10   # 只涨一次，且是明确拒绝的那一次没生效
    assert after["rowVersion"] == version + 1

    entries = client.get("/api/audit").json()["entries"]
    assert {e["status"] for e in entries[:2]} == {"applied", "rejected"}


# --- 审计 -----------------------------------------------------------

def test_audit_records_actor_and_outcome(client):
    as_(client, "xiaozhang").post("/api/actions/createOrder/apply",
                                  json={"params": {"buyer": "B-1", "shipTo": "上海"}})
    as_(client, "xiaozhang").post("/api/actions/addLine/apply",
                                  json={"params": {"order": "O-1001", "product": "HUB-C8", "qty": 1}})
    entries = client.get("/api/audit").json()["entries"]
    assert entries[0]["status"] == "rejected"      # 最新一条是失败的 addLine
    assert entries[1]["status"] == "applied"
    assert all(e["actor"] == "xiaozhang" for e in entries[:2])


# --- 改本体 ---------------------------------------------------------

def test_add_property_requires_existing_column(client):
    as_(client, "admin")
    r = client.post("/api/ontology/properties/Order",
                    json={"apiName": "discount", "displayName": "折扣",
                          "dataType": "currency", "sourceColumn": "discount_amt"})
    assert r.status_code == 422
    assert "没有 discount_amt 这一列" in r.json()["error"]


def test_add_property_maps_real_column(client):
    """orders.carrier_cd 本来已经映射了；换个没映射的列来演示。"""
    as_(client, "admin")
    client.delete("/api/ontology/properties/Order/carrier")
    r = client.post("/api/ontology/properties/Order",
                    json={"apiName": "carrier2", "displayName": "承运商",
                          "dataType": "string", "sourceColumn": "carrier_cd"})
    assert r.status_code == 200
    order = client.get("/api/objects/Order/O-1001").json()["object"]
    assert order["props"]["carrier2"] == "顺丰"


def test_removing_property_keeps_business_data(client):
    """解除映射不删数据 —— 和演练场"删属性=删数据"完全不同。"""
    as_(client, "admin")
    client.delete("/api/ontology/properties/Order/shipTo")
    order = client.get("/api/objects/Order/O-1001").json()["object"]
    assert "shipTo" not in order["props"]
    # 业务表里那一列还在
    tables = {t["name"]: t for t in client.get("/api/tables").json()["tables"]}
    assert any(c["name"] == "ship_to_city" for c in tables["orders"]["columns"])


def test_schema_changes_are_logged(client):
    as_(client, "admin").patch("/api/ontology/object-types/Order",
                               json={"displayName": "销售订单"})
    entries = client.get("/api/schema-log").json()["entries"]
    assert entries and "销售订单" in entries[0]["summary"]


# --- 新建三种类型 ----------------------------------------------------

def test_bind_table_as_object_type_skips_foreign_keys(client):
    """绑定 shipment 表：order_no 是外键，不该变成属性。"""
    as_(client, "admin")
    r = client.post("/api/ontology/object-types",
                    json={"apiName": "Shipment", "displayName": "运单",
                          "sourceTable": "shipment", "pkColumn": "tracking_no", "idPrefix": "S"})
    assert r.status_code == 200
    data = r.json()
    assert "trackingNo" in data["mapped"]
    assert "orderNo" not in data["mapped"]                      # 外键被跳过
    assert [s["column"] for s in data["skippedForeignKeys"]] == ["order_no"]
    assert "row_version" in data["skippedOther"]
    assert data["titleProp"] == "trackingNo"                    # 挑了运单号而不是承运商

    obj = client.get("/api/objects/Shipment/SF-7788-2103").json()["object"]
    assert obj["title"] == "SF-7788-2103"
    assert "orderNo" not in obj["props"]


def test_cannot_bind_nonexistent_or_taken_table(client):
    as_(client, "admin")
    r = client.post("/api/ontology/object-types",
                    json={"apiName": "Ghost", "sourceTable": "no_such_table", "pkColumn": "id"})
    assert r.status_code == 422 and "没有 no_such_table 这张表" in r.json()["error"]

    r = client.post("/api/ontology/object-types",
                    json={"apiName": "Order2", "sourceTable": "orders", "pkColumn": "order_no"})
    assert r.status_code == 409 and "已经绑定给对象类型 Order" in r.json()["error"]


def test_link_must_be_backed_by_real_foreign_key(client):
    as_(client, "admin")
    client.post("/api/ontology/object-types",
                json={"apiName": "Shipment", "sourceTable": "shipment", "pkColumn": "tracking_no"})

    r = client.post("/api/ontology/link-types",
                    json={"apiName": "bogus", "fromType": "Order", "toType": "Shipment",
                          "fkTable": "shipment", "fkColumn": "made_up_col"})
    assert r.status_code == 422 and "不是一个外键" in r.json()["error"]

    r = client.post("/api/ontology/link-types",
                    json={"apiName": "fulfilledBy", "fromType": "Order", "toType": "Shipment",
                          "cardinality": "1 : N", "fwdLabel": "履约运单", "invLabel": "所属订单",
                          "fkTable": "shipment", "fkColumn": "order_no"})
    assert r.status_code == 200

    links = client.get("/api/objects/Order/O-1001").json()["links"]
    group = next(g for g in links if g["link"] == "fulfilledBy")
    assert {t["pk"] for t in group["targets"]} == {"SF-7788-2103", "JD-1122-5540"}


def test_new_action_type_is_runnable_immediately(client):
    """操作类型是纯元数据，存进去就能跑 —— 不依赖任何表结构改动。"""
    as_(client, "admin")
    client.post("/api/ontology/object-types",
                json={"apiName": "Shipment", "sourceTable": "shipment", "pkColumn": "tracking_no"})
    r = client.put("/api/ontology/action-types/markDelivered", json={
        "displayName": "标记签收", "requiredRole": "仓管",
        "params": [{"id": "shipment", "cn": "运单", "kind": "object", "objectType": "Shipment"}],
        "rules": [{"type": "compare",
                   "left": {"src": "prop", "of": {"kind": "param", "param": "shipment"}, "prop": "shipStatus"},
                   "op": "==", "right": {"src": "const", "value": "运输中"}}],
        "edits": [{"uid": "e1", "op": "modify", "target": {"kind": "param", "param": "shipment"},
                   "prop": "shipStatus", "mode": "set", "value": {"src": "const", "value": "已签收"}}],
    })
    assert r.status_code == 200

    r = as_(client, "xiaozhang").post("/api/actions/markDelivered/apply",
                                      json={"params": {"shipment": "JD-1122-5540"}})
    assert r.status_code == 403                                  # 客服不行

    r = as_(client, "laowang").post("/api/actions/markDelivered/apply",
                                    json={"params": {"shipment": "JD-1122-5540"}})
    assert r.status_code == 200
    obj = client.get("/api/objects/Shipment/JD-1122-5540").json()["object"]
    assert obj["props"]["shipStatus"] == "已签收"

    # 已签收的不能再签收一次
    r = client.post("/api/actions/markDelivered/apply",
                    json={"params": {"shipment": "JD-1122-5540"}})
    assert r.status_code == 422


def test_unbinding_is_blocked_while_referenced(client):
    as_(client, "admin")
    client.post("/api/ontology/object-types",
                json={"apiName": "Shipment", "sourceTable": "shipment", "pkColumn": "tracking_no"})
    client.post("/api/ontology/link-types",
                json={"apiName": "fulfilledBy", "fromType": "Order", "toType": "Shipment",
                      "fkTable": "shipment", "fkColumn": "order_no"})

    r = client.delete("/api/ontology/object-types/Shipment")
    assert r.status_code == 409 and "fulfilledBy" in r.json()["error"]

    client.delete("/api/ontology/link-types/fulfilledBy")
    r = client.delete("/api/ontology/object-types/Shipment")
    assert r.status_code == 200
    assert r.json()["sourceTableKept"] == "shipment"

    # 解除绑定不删数据：表还在，行也还在
    tables = {t["name"] for t in client.get("/api/tables").json()["tables"]}
    assert "shipment" in tables
    assert client.get("/api/objects/Shipment/SF-7788-2103").status_code == 404
