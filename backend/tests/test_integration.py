"""端到端冒烟：不调模型，但走完真实的工具链路。

这一层测的是「模型之外的一切」都是通的：schema 内省 → 召回 → 只读校验 →
执行 → 结果落沙箱文件。模型换谁都不影响这条链路，所以它值得被牢牢钉住。
"""

from __future__ import annotations

import json

from agentbase.evalkit.cases import EvalCase
from agentbase.evalkit.runner import evaluate_retrieval
from agentbase.tools.registry import build_tools


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


def test_schema_introspection_captures_columns(schema_cache):
    orders = schema_cache.find("shop", "orders")
    assert orders is not None
    names = {c.name for c in orders.columns}
    assert {"order_id", "amount", "status", "is_test"} <= names
    assert "CREATE TABLE orders" in orders.to_ddl()


def test_retriever_finds_relevant_table(run_context):
    hits = run_context.retriever.search("订单 金额", datasource="shop", limit=5)
    assert any(t.name == "orders" for t in hits)


def test_search_tables_tool(run_context):
    out = _tool(build_tools(run_context), "search_tables").invoke(
        {"query": "customer 客户", "datasource": "shop"}
    )
    assert "customers" in out


def test_describe_table_tool_reports_missing_table(run_context):
    out = _tool(build_tools(run_context), "describe_table").invoke(
        {"datasource": "shop", "table": "不存在的表"}
    )
    assert "找不到表" in out


def test_run_sql_executes_and_reports_rows(run_context):
    out = _tool(build_tools(run_context), "run_sql").invoke(
        {
            "datasource": "shop",
            "sql": (
                "SELECT sum(amount) AS gmv FROM orders "
                "WHERE status IN ('paid','completed') AND is_test = 0"
            ),
            "purpose": "算 GMV",
        }
    )
    assert "35000" in out
    assert run_context.executed_sql, "执行过的 SQL 应被记录下来供评测和审计"


def test_run_sql_rejects_write(run_context):
    out = _tool(build_tools(run_context), "run_sql").invoke(
        {"datasource": "shop", "sql": "DELETE FROM orders", "purpose": "清理"}
    )
    assert "只允许 SELECT" in out
    assert not run_context.executed_sql


def test_run_sql_enforces_role_allowlist(run_context, config):
    run_context.role = config.role("销售")  # 只被授权 customers
    out = _tool(build_tools(run_context), "run_sql").invoke(
        {"datasource": "shop", "sql": "SELECT * FROM orders", "purpose": "越权尝试"}
    )
    assert "无权访问" in out


def test_run_sql_saves_full_result_to_sandbox(run_context):
    out = _tool(build_tools(run_context), "run_sql").invoke(
        {
            "datasource": "shop",
            "sql": "SELECT * FROM orders",
            "purpose": "导明细",
            "save_as": "orders.csv",
        }
    )
    assert "orders.csv" in out
    csv_text = run_context.sandbox.read_file("workspace/orders.csv")
    assert csv_text.splitlines()[0].startswith("order_id")


def test_sandbox_bash_has_no_credentials_in_env(run_context):
    """沙箱里跑的是模型生成的代码，绝不能看得到网关的密钥。"""
    tools = build_tools(run_context)
    out = _tool(tools, "bash").invoke({"command": "env"})
    assert "DASHSCOPE_API_KEY" not in out
    assert "DB_PASSWORD" not in out


def test_sandbox_blocks_path_traversal(run_context):
    out = _tool(build_tools(run_context), "read_file").invoke({"path": "../../../etc/passwd"})
    assert "越界" in out


def test_read_skill_returns_body_not_catalog(run_context):
    out = _tool(build_tools(run_context), "read_skill").invoke({"name": "指标口径"})
    assert "GMV" in out and "is_test" in out


def test_retrieval_eval_scores_cases(run_context, registry):
    cases = [
        EvalCase(
            id="gmv-01",
            question="上个月的 GMV 是多少",
            datasource="shop",
            golden_sql="SELECT sum(amount) FROM orders WHERE status='paid'",
        ),
        EvalCase(
            id="cust-01",
            question="华东区有哪些客户",
            datasource="shop",
            golden_sql="SELECT name FROM customers WHERE region='华东'",
        ),
    ]
    report = evaluate_retrieval(cases, run_context.retriever, registry, top_k=5)
    assert report.total == 2
    assert report.accuracy == 1.0
    assert "准确率" in report.render()


def test_schema_cache_roundtrip(schema_cache):
    schema_cache.save()
    payload = json.loads(schema_cache.path.read_text(encoding="utf-8"))
    assert "shop" in payload
    assert schema_cache.load() is True
    assert schema_cache.find("shop", "orders") is not None
