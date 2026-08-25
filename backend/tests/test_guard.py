"""只读校验的测试。

这是安全边界，所以测试写得比其他地方细。每一条都对应一种真实的失败方式：
模型自作主张写 DML、提示注入拼接第二条语句、用函数读本地文件、越权访问表。
"""

from __future__ import annotations

import pytest

from agentbase.datasources.guard import SQLGuardError, guard_sql

DSN = "postgresql://u:p@h/db"


def test_allows_select_and_injects_limit():
    got = guard_sql("SELECT * FROM orders", dsn=DSN, max_rows=100)
    assert got.limit_applied == 100
    assert "LIMIT 100" in got.sql.upper()
    assert got.tables == ("orders",)


def test_keeps_explicit_limit():
    got = guard_sql("SELECT * FROM orders LIMIT 5", dsn=DSN, max_rows=100)
    assert got.limit_applied is None


def test_allows_cte_and_ignores_cte_alias_as_table():
    got = guard_sql(
        "WITH paid AS (SELECT * FROM orders WHERE status='paid') SELECT count(*) FROM paid",
        dsn=DSN,
        max_rows=100,
    )
    assert "orders" in got.tables
    assert "paid" not in got.tables


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE orders SET amount = 0",
        "INSERT INTO orders VALUES (1)",
        "DROP TABLE orders",
        "TRUNCATE orders",
        "CREATE TABLE t (a int)",
        "ALTER TABLE orders ADD COLUMN x int",
        "GRANT ALL ON orders TO public",
    ],
)
def test_rejects_writes(sql):
    with pytest.raises(SQLGuardError):
        guard_sql(sql, dsn=DSN, max_rows=100)


def test_rejects_stacked_statements():
    # 提示注入最常见的形态：在一条正常查询后面拼一条破坏性语句。
    with pytest.raises(SQLGuardError, match="一次只能执行一条语句"):
        guard_sql("SELECT 1; DROP TABLE orders", dsn=DSN, max_rows=100)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_sleep(100)",
        "SELECT load_file('/etc/passwd')",
        "SELECT * FROM orders INTO OUTFILE '/tmp/x'",
    ],
)
def test_rejects_dangerous_functions(sql):
    with pytest.raises(SQLGuardError):
        guard_sql(sql, dsn=DSN, max_rows=100)


def test_table_allowlist_blocks_unlisted_table():
    with pytest.raises(SQLGuardError, match="无权访问"):
        guard_sql("SELECT * FROM salaries", dsn=DSN, max_rows=100, table_allowlist=["orders"])


def test_table_allowlist_supports_schema_wildcard():
    got = guard_sql(
        "SELECT * FROM public.orders", dsn=DSN, max_rows=100, table_allowlist=["public.*"]
    )
    assert got.tables == ("public.orders",)


def test_allowlist_checks_every_joined_table():
    # 只校验第一张表是个经典漏洞，JOIN 进来的表同样要过白名单。
    with pytest.raises(SQLGuardError, match="salaries"):
        guard_sql(
            "SELECT * FROM orders o JOIN salaries s ON o.customer_id = s.id",
            dsn=DSN,
            max_rows=100,
            table_allowlist=["orders"],
        )


def test_rejects_empty_sql():
    with pytest.raises(SQLGuardError):
        guard_sql("   ", dsn=DSN, max_rows=100)
