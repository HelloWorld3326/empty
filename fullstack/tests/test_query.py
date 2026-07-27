"""查询层测试：映射、值转换、链接遍历生成的 SQL。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import ontology, query  # noqa: E402
from server.db import connect  # noqa: E402
from server.initdb import init  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    db = tmp_path / "t.db"
    init(db, quiet=True)
    c = connect(db)
    yield c
    c.raw.close()


@pytest.fixture()
def onto(conn):
    return ontology.load(conn)


# --- 映射与值转换 ---------------------------------------------------

def test_columns_map_to_property_names(conn, onto):
    buyer = query.get_object(conn, onto.ot("Buyer"), "B-1")
    assert buyer["props"]["name"] == "周韫"      # full_name → name
    assert buyer["title"] == "周韫"
    assert "full_name" not in buyer["props"]     # 列名不应该漏出去


def test_code_value_becomes_label(conn, onto):
    """member_lvl 库里存 '03'，本体层必须是「金卡」。"""
    assert conn.one("SELECT member_lvl FROM buyer WHERE buyer_id='B-1'")["member_lvl"] == "03"
    buyer = query.get_object(conn, onto.ot("Buyer"), "B-1")
    assert buyer["props"]["level"] == "金卡"


def test_cents_become_yuan(conn, onto):
    assert conn.one("SELECT unit_price FROM product WHERE sku='AUD-Q30'")["unit_price"] == 89900
    p = query.get_object(conn, onto.ot("Product"), "AUD-Q30")
    assert p["props"]["price"] == 899.0


def test_scale_roundtrip_is_exact():
    """899.99 * 100 在浮点下是 89998.999...，不 round 就会少一分。"""
    prop = ontology.Property("price", "单价", "currency", "unit_price", scale=100)
    assert prop.to_storage(899.99) == 89999
    assert prop.to_display(89999) == 899.99


def test_foreign_key_is_not_a_property(conn, onto):
    """orders.buyer_id 是链接，不是 Order 的属性 —— 这是本体的核心转换。"""
    order = query.get_object(conn, onto.ot("Order"), "O-1001")
    assert "buyerId" not in order["props"]
    assert onto.ot("Order").column_of("status") == "ord_status"
    assert all(p.source_column != "buyer_id" for p in onto.ot("Order").props)


# --- 链接遍历 -------------------------------------------------------

def test_fk_on_to_side_forward_is_single_table(onto):
    """placed 正向：外键在 orders 上，一次单表过滤就够，不用 JOIN。"""
    sql, target = query.traverse_sql(onto, onto.lt("placed"), "fwd")
    assert target.api_name == "Order"
    assert "JOIN" not in sql.upper()
    assert "orders" in sql and "buyer_id" in sql


def test_fk_on_to_side_inverse_needs_join(onto):
    """placed 反向：从订单找买家，必须 JOIN 回 buyer 表。"""
    sql, target = query.traverse_sql(onto, onto.lt("placed"), "inv")
    assert target.api_name == "Buyer"
    assert "JOIN" in sql.upper()


def test_fk_on_from_side_forward_needs_join(onto):
    """ofProduct 正向：外键在 order_line 上，要 JOIN 到 product。"""
    sql, target = query.traverse_sql(onto, onto.lt("ofProduct"), "fwd")
    assert target.api_name == "Product"
    assert "JOIN" in sql.upper()


def test_traverse_returns_real_rows(conn, onto):
    lines = query.traverse(conn, onto, onto.lt("hasLine"), "fwd", "O-1001")
    assert {l["pk"] for l in lines} == {"L-1", "L-2"}

    order = query.traverse(conn, onto, onto.lt("hasLine"), "inv", "L-1")
    assert [o["pk"] for o in order] == ["O-1001"]

    buyer = query.traverse(conn, onto, onto.lt("placed"), "inv", "O-1001")
    assert buyer[0]["props"]["name"] == "周韫"


def test_two_hops_in_opposite_directions(conn, onto):
    """订单行 → 所属订单（反向）、订单行 → 对应商品（正向）。"""
    order = query.traverse(conn, onto, onto.lt("hasLine"), "inv", "L-1")[0]
    product = query.traverse(conn, onto, onto.lt("ofProduct"), "fwd", "L-1")[0]
    assert order["pk"] == "O-1001"
    assert product["pk"] == "AUD-Q30"


def test_links_of_object_groups_both_directions(conn, onto):
    groups = query.links_of_object(conn, onto, onto.ot("Order"), "O-1001")
    by_link = {(g["link"], g["dir"]): g for g in groups}
    assert ("placed", "inv") in by_link     # 下单买家
    assert ("hasLine", "fwd") in by_link    # 订单行
    assert len(by_link[("hasLine", "fwd")]["targets"]) == 2


def test_graph_edges_reference_existing_nodes(conn, onto):
    g = query.graph(conn, onto)
    keys = {n["key"] for n in g["nodes"]}
    assert g["edges"], "应该有边"
    for e in g["edges"]:
        assert e["a"] in keys and e["b"] in keys


# --- SQL 追踪与注入防护 ---------------------------------------------

def test_sql_trace_records_queries(conn, onto):
    before = len(conn.trace.entries)
    query.get_object(conn, onto.ot("Order"), "O-1001")
    assert len(conn.trace.entries) > before
    assert "SELECT" in conn.trace.entries[-1]["sql"].upper()


def test_bad_identifier_is_rejected():
    with pytest.raises(ValueError):
        query.ident("orders; DROP TABLE buyer")
