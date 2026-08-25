"""Schema 召回的行为边界。

这里刻意写了一个「预期失败」的用例：中文提问 + 英文表名 + 无注释 + 大库，
关键词召回必然失灵。写出来是为了防止有人误以为默认实现能扛住这种情况——
真遇到这个场景，要么补别名，要么换向量召回，没有第三条路。
"""

from __future__ import annotations

from agentbase.datasources.introspect import ColumnInfo, SchemaCache, TableInfo
from agentbase.datasources.retriever import KeywordSchemaRetriever


def _cache(tables: list[TableInfo], tmp_path) -> SchemaCache:
    cache = SchemaCache(tmp_path / "schema.json")
    cache._tables = {"ds": tables}
    return cache


def _table(name: str, comment: str | None = None, columns: list[str] | None = None) -> TableInfo:
    return TableInfo(
        datasource="ds",
        schema=None,
        name=name,
        comment=comment,
        columns=[ColumnInfo(name=c, type="TEXT") for c in (columns or ["id"])],
    )


def test_matches_on_chinese_table_comment(tmp_path):
    """注释齐全时，中文提问能直接命中——这是最理想的情况。"""
    cache = _cache(
        [_table("t_ord_inf_02", comment="订单主表"), _table("t_usr_log", comment="用户登录日志")],
        tmp_path,
    )
    hits = KeywordSchemaRetriever(cache).search("订单相关的数据", limit=1)
    assert hits[0].name == "t_ord_inf_02"


def test_matches_on_english_table_name(tmp_path):
    cache = _cache([_table("orders"), _table("customers")], tmp_path)
    hits = KeywordSchemaRetriever(cache).search("orders by amount", limit=1)
    assert hits[0].name == "orders"


def test_alias_bridges_chinese_query_to_english_table(tmp_path):
    """别名是无注释库的救命稻草：一行配置就能把中文问题接到英文表上。"""
    cache = _cache([_table(f"tbl_{i}") for i in range(80)] + [_table("orders")], tmp_path)
    retriever = KeywordSchemaRetriever(cache, aliases={"ds": {"orders": ["订单", "GMV", "销售额"]}})
    hits = retriever.search("上个月的 GMV 是多少", limit=3)
    assert hits[0].name == "orders"


def test_small_schema_falls_back_to_all_tables(tmp_path):
    """表少时召回不足会退化成全量返回——这时候本来就该把 schema 全给模型看。"""
    cache = _cache([_table("orders"), _table("customers")], tmp_path)
    hits = KeywordSchemaRetriever(cache).search("完全不相关的问题", limit=10)
    assert len(hits) == 2


def test_large_schema_without_comments_or_aliases_recalls_nothing(tmp_path):
    """已知短板，写下来防止误判。

    大库 + 英文表名 + 无注释 + 无别名 = 关键词召回归零。补别名或换向量召回，
    没有第三条路。换之前先用 evalkit.evaluate_retrieval 跑基线。
    """
    cache = _cache([_table(f"t_{i:03d}") for i in range(200)], tmp_path)
    hits = KeywordSchemaRetriever(cache).search("上个月华东区的退款率", limit=10)
    assert hits == []


def test_cte_alias_not_treated_as_table_in_ranking(tmp_path):
    cache = _cache([_table("orders", comment="订单"), _table("refunds", comment="退款")], tmp_path)
    hits = KeywordSchemaRetriever(cache).search("退款", limit=1)
    assert hits[0].name == "refunds"
