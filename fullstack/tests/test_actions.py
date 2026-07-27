"""Action 引擎测试：校验、编辑生成、事务原子性、乐观锁。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import actions, ontology, query  # noqa: E402
from server.db import connect  # noqa: E402
from server.initdb import init  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    init(path, quiet=True)
    return path


@pytest.fixture()
def conn(db):
    c = connect(db)
    yield c
    c.raw.close()


@pytest.fixture()
def onto(conn):
    return ontology.load(conn)


def run(conn, onto, action_id, params, actor="admin", versions=None):
    return actions.apply(conn, onto, onto.at(action_id), params, actor, versions)


def obj(conn, onto, ty, pk):
    return query.get_object(conn, onto.ot(ty), pk)


# --- 校验 -----------------------------------------------------------

def test_rules_are_described_in_chinese(conn, onto):
    pre = actions.preview(conn, onto, onto.at("addLine"),
                          {"order": "O-1002", "product": "AUD-Q30", "qty": 1})
    texts = [r["cn"] for r in pre["rules"]]
    assert "订单.状态 等于 待支付" in texts
    assert "商品.库存 大于等于 数量" in texts


def test_status_rule_blocks_completed_order(conn, onto):
    pre = actions.preview(conn, onto, onto.at("addLine"),
                          {"order": "O-1001", "product": "AUD-Q30", "qty": 1})
    assert not pre["valid"]
    failed = [r for r in pre["rules"] if r["state"] == "fail"]
    assert failed and "已完成" in failed[0]["why"]


def test_insufficient_stock_blocks(conn, onto):
    pre = actions.preview(conn, onto, onto.at("addLine"),
                          {"order": "O-1002", "product": "AUD-Q30", "qty": 9999})
    assert not pre["valid"]


def test_empty_order_cannot_be_paid(conn, onto):
    """linkCount 规则：没有订单行就不能支付。"""
    run(conn, onto, "createOrder", {"buyer": "B-2", "shipTo": "成都"})
    new_pk = obj(conn, onto, "Order", "O-1003")["pk"]
    pre = actions.preview(conn, onto, onto.at("payOrder"), {"order": new_pk})
    assert not pre["valid"]
    assert any("数量" in r["cn"] and r["state"] == "fail" for r in pre["rules"])


def test_incomplete_params_are_idle_not_failed(conn, onto):
    pre = actions.preview(conn, onto, onto.at("addLine"), {"order": "O-1002"})
    assert not pre["complete"]
    assert all(r["state"] == "idle" for r in pre["rules"])


# --- 编辑生成与写入 --------------------------------------------------

def test_create_order_writes_fk_column(conn, onto):
    """建立链接 = 写外键列。"""
    res = run(conn, onto, "createOrder", {"buyer": "B-2", "shipTo": "成都"})
    ops = [e["op"] for e in res["edits"]]
    assert ops == ["create", "link"]

    link_edit = res["edits"][1]
    assert link_edit["table"] == "orders" and link_edit["fkColumn"] == "buyer_id"

    row = conn.one("SELECT buyer_id, ord_status FROM orders WHERE order_no='O-1003'")
    assert row["buyer_id"] == "B-2"
    assert row["ord_status"] == "待支付"


def test_add_line_writes_three_objects(conn, onto):
    run(conn, onto, "createOrder", {"buyer": "B-2", "shipTo": "成都"})
    before = obj(conn, onto, "Product", "KBD-K7")["props"]["stock"]

    res = run(conn, onto, "addLine", {"order": "O-1003", "product": "KBD-K7", "qty": 3})
    assert [e["op"] for e in res["edits"]] == ["create", "link", "link", "modify"]

    line = obj(conn, onto, "OrderLine", "L-4")
    assert line["props"]["qty"] == 3
    assert line["props"]["unitPrice"] == 459.0
    assert line["props"]["subtotal"] == 459.0 * 3          # calc 求值
    assert obj(conn, onto, "Product", "KBD-K7")["props"]["stock"] == before - 3

    # 小计在库里是分
    assert conn.one("SELECT subtotal FROM order_line WHERE line_no='L-4'")["subtotal"] == 137700


def test_currency_param_is_scaled_on_write(conn, onto):
    """上架商品时输入 1899 元，库里必须是 189900 分。"""
    run(conn, onto, "createProduct",
        {"sku": "DSK-A1", "name": "升降桌 A1", "price": 1899, "stock": 5})
    assert conn.one("SELECT unit_price FROM product WHERE sku='DSK-A1'")["unit_price"] == 189900
    assert obj(conn, onto, "Product", "DSK-A1")["props"]["price"] == 1899.0


def test_explicit_pk_from_param(conn, onto):
    """SKU 由业务给定，不是生成的。"""
    res = run(conn, onto, "createProduct",
              {"sku": "LMP-Z9", "name": "台灯 Z9", "price": 99, "stock": 3})
    assert res["edits"][0]["pk"] == "LMP-Z9"


def test_remove_line_hops_both_directions(conn, onto):
    """参数只给订单行：规则走反向 hop 看订单状态，编辑走正向 hop 退库存。"""
    stock_before = obj(conn, onto, "Product", "KBD-K7")["props"]["stock"]
    res = run(conn, onto, "removeLine", {"line": "L-3"})   # L-3 属于待支付的 O-1002
    assert [e["op"] for e in res["edits"]] == ["modify", "delete"]
    assert obj(conn, onto, "Product", "KBD-K7")["props"]["stock"] == stock_before + 1
    assert obj(conn, onto, "OrderLine", "L-3") is None


def test_remove_line_blocked_on_paid_order(conn, onto):
    run(conn, onto, "payOrder", {"order": "O-1002"})
    with pytest.raises(actions.ValidationError):
        run(conn, onto, "removeLine", {"line": "L-3"})
    assert obj(conn, onto, "OrderLine", "L-3") is not None   # 没被删掉


def test_full_lifecycle(conn, onto):
    run(conn, onto, "createOrder", {"buyer": "B-4", "shipTo": "广州"})
    run(conn, onto, "addLine", {"order": "O-1003", "product": "HUB-C8", "qty": 2})
    run(conn, onto, "addLine", {"order": "O-1003", "product": "MON-U27", "qty": 1})
    run(conn, onto, "payOrder", {"order": "O-1003"})
    run(conn, onto, "shipOrder", {"order": "O-1003", "carrier": "中通"})
    run(conn, onto, "completeOrder", {"order": "O-1003"})

    o = obj(conn, onto, "Order", "O-1003")
    assert o["props"]["status"] == "已完成"
    assert o["props"]["carrier"] == "中通"
    lines = query.traverse(conn, onto, onto.lt("hasLine"), "fwd", "O-1003")
    assert len(lines) == 2
    assert sum(l["props"]["subtotal"] for l in lines) == 199.0 * 2 + 2299.0


# --- 事务原子性 ------------------------------------------------------

def test_failed_action_writes_nothing(conn, onto):
    """校验失败时，一条编辑都不能落地。"""
    before = obj(conn, onto, "Product", "AUD-Q30")["props"]["stock"]
    order_count = conn.one("SELECT COUNT(*) n FROM orders")["n"]
    with pytest.raises(actions.ValidationError):
        run(conn, onto, "addLine", {"order": "O-1001", "product": "AUD-Q30", "qty": 1})
    assert obj(conn, onto, "Product", "AUD-Q30")["props"]["stock"] == before
    assert conn.one("SELECT COUNT(*) n FROM orders")["n"] == order_count
    assert conn.one("SELECT COUNT(*) n FROM order_line WHERE line_no='L-4'")["n"] == 0


def test_rejection_is_still_logged(conn, onto):
    with pytest.raises(actions.ValidationError):
        run(conn, onto, "addLine", {"order": "O-1001", "product": "AUD-Q30", "qty": 1},
            actor="xiaozhang")
    row = conn.one("SELECT * FROM action_log ORDER BY id DESC LIMIT 1")
    assert row["status"] == "rejected"
    assert row["actor"] == "xiaozhang"
    assert "校验未通过" in row["error"]


def test_success_is_logged_with_edits(conn, onto):
    run(conn, onto, "createOrder", {"buyer": "B-1", "shipTo": "上海"}, actor="xiaozhang")
    row = conn.one("SELECT * FROM action_log ORDER BY id DESC LIMIT 1")
    assert row["status"] == "applied"
    assert row["action_type"] == "createOrder"
    assert "create" in row["edits"]


# --- 乐观锁 ----------------------------------------------------------

def test_stale_version_is_rejected(conn, onto):
    """A 和 B 都读到版本 1；A 先支付，B 再提交就该撞车。"""
    order = obj(conn, onto, "Order", "O-1002")
    stale = {f"Order:O-1002": order["rowVersion"]}

    run(conn, onto, "payOrder", {"order": "O-1002"}, versions=dict(stale))
    assert obj(conn, onto, "Order", "O-1002")["rowVersion"] == order["rowVersion"] + 1

    with pytest.raises(actions.ConflictError):
        run(conn, onto, "cancelOrder", {"order": "O-1002", "reason": "缺货"}, versions=stale)
    assert obj(conn, onto, "Order", "O-1002")["props"]["status"] == "已支付"   # 没被改掉


def test_conflict_rolls_back_everything(conn, onto):
    """撞车时整个 Action 回滚，不能只写一半。"""
    run(conn, onto, "createOrder", {"buyer": "B-2", "shipTo": "成都"})
    product_before = obj(conn, onto, "Product", "HUB-C8")["props"]["stock"]
    stale_product = {"Product:HUB-C8": 999}      # 一个绝不可能匹配的版本

    with pytest.raises(actions.ConflictError):
        run(conn, onto, "addLine", {"order": "O-1003", "product": "HUB-C8", "qty": 2},
            versions=stale_product)

    assert obj(conn, onto, "Product", "HUB-C8")["props"]["stock"] == product_before
    assert obj(conn, onto, "OrderLine", "L-4") is None       # 订单行也没留下
    row = conn.one("SELECT * FROM action_log ORDER BY id DESC LIMIT 1")
    assert row["status"] == "rejected"


def test_fresh_version_succeeds(conn, onto):
    order = obj(conn, onto, "Order", "O-1002")
    run(conn, onto, "payOrder", {"order": "O-1002"},
        versions={"Order:O-1002": order["rowVersion"]})
    assert obj(conn, onto, "Order", "O-1002")["props"]["status"] == "已支付"


def test_sequence_advances_per_created_object(conn, onto):
    run(conn, onto, "createOrder", {"buyer": "B-1", "shipTo": "上海"})
    run(conn, onto, "createOrder", {"buyer": "B-2", "shipTo": "成都"})
    assert obj(conn, onto, "Order", "O-1003") is not None
    assert obj(conn, onto, "Order", "O-1004") is not None
