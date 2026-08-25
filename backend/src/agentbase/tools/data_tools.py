"""数据相关工具 —— 全部在网关侧执行，凭证不下沉到沙箱。

三个工具构成一个刻意设计的漏斗：

    search_tables  →  describe_table  →  run_sql

模型必须先找到表、再看清结构、才能写 SQL。工具描述里会明确要求这个顺序。
这不是形式主义：让模型不看 schema 直接写 SQL，是问数场景下最主要的错误来源
——它会照着常见命名习惯编字段名，SQL 跑得通，结果是错的。
"""

from __future__ import annotations

import csv
import io
import logging

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..datasources.guard import SQLGuardError
from .context import RunContext

logger = logging.getLogger(__name__)


class SearchTablesInput(BaseModel):
    query: str = Field(description="要查找的业务概念，用中文即可，例如「订单 退款 金额」")
    datasource: str | None = Field(default=None, description="限定数据源名；不填则搜全部有权限的源")
    limit: int = Field(default=15, description="返回表数量上限")


class DescribeTableInput(BaseModel):
    datasource: str = Field(description="数据源名")
    table: str = Field(description="表名，可带 schema 前缀，如 public.orders")


class RunSQLInput(BaseModel):
    datasource: str = Field(description="数据源名")
    sql: str = Field(description="要执行的 SELECT 语句。只读，一次一条。")
    purpose: str = Field(
        description="一句话说明这条 SQL 想回答什么问题。会展示给用户做确认，请写清楚。"
    )
    save_as: str | None = Field(
        default=None,
        description="把完整结果存成沙箱 CSV 文件的文件名，如 orders.csv。"
        "结果超过几十行时必须填，后续用 python 读这个文件做分析。",
    )


def build_data_tools(ctx: RunContext) -> list[StructuredTool]:
    def search_tables(query: str, datasource: str | None = None, limit: int = 15) -> str:
        if datasource:
            try:
                ctx.datasources.check_access(datasource, ctx.role)
            except PermissionError as exc:
                return f"错误: {exc}"
        tables = ctx.retriever.search(query, datasource=datasource, limit=limit)
        if not tables:
            return (
                f"没有找到与「{query}」相关的表。请换个说法再试，"
                f"或先看看可用数据源:\n{ctx.datasources.describe_all(ctx.role)}"
            )
        lines = [f"找到 {len(tables)} 张相关表（按相关度排序）:"]
        lines.extend(f"  [{t.datasource}] {t.to_card()}" for t in tables)
        lines.append("\n下一步：对准备使用的表调用 describe_table 看清字段，再写 SQL。")
        return "\n".join(lines)

    def describe_table(datasource: str, table: str) -> str:
        try:
            ctx.datasources.check_access(datasource, ctx.role)
        except PermissionError as exc:
            return f"错误: {exc}"
        info = ctx.schema_cache.find(datasource, table)
        if info is None:
            return (
                f"数据源 `{datasource}` 里找不到表 `{table}`。"
                "请先用 search_tables 确认表名，注意可能需要带 schema 前缀。"
            )
        return info.to_ddl()

    def run_sql(
        datasource: str, sql: str, purpose: str, save_as: str | None = None
    ) -> str:
        try:
            guarded = ctx.datasources.prepare(datasource, sql, ctx.role)
        except PermissionError as exc:
            return f"权限错误: {exc}"
        except SQLGuardError as exc:
            # 报错原样回给模型，让它自己改。不要代它改写 SQL。
            return f"SQL 未通过安全校验: {exc}"
        except KeyError as exc:
            return f"错误: {exc}。可用数据源:\n{ctx.datasources.describe_all(ctx.role)}"

        if ctx.require_sql_confirmation and not _confirm(datasource, guarded.sql, purpose):
            return "用户拒绝执行这条 SQL。请询问用户具体哪里不对，不要直接重试。"

        try:
            result = ctx.datasources.execute(datasource, sql, ctx.role, prepared=guarded)
        except Exception as exc:
            logger.warning("SQL 执行失败 ds=%s: %s", datasource, exc)
            return f"SQL 执行失败: {type(exc).__name__}: {exc}"

        ctx.executed_sql.append(guarded.sql)
        ds_cfg = ctx.datasources.config_for(datasource)

        saved_note = ""
        if save_as:
            path = f"workspace/{save_as}"
            ctx.sandbox.write_file(path, _to_csv(result.columns, result.rows))
            saved_note = f"\n完整结果（{result.row_count} 行）已存到沙箱 {path}，可用 pandas 读取。"

        return result.to_model_text(ds_cfg.max_rows_to_model) + saved_note

    return [
        StructuredTool.from_function(
            func=search_tables,
            name="search_tables",
            description=(
                "按业务概念检索数据表。写 SQL 前必须先用它找到相关表——"
                "不要凭猜测使用表名。"
            ),
            args_schema=SearchTablesInput,
        ),
        StructuredTool.from_function(
            func=describe_table,
            name="describe_table",
            description=(
                "查看一张表的完整字段结构和注释。写 SQL 前必须对每张要用到的表调用它，"
                "确认字段名和含义。跳过这一步直接写 SQL 会导致字段名幻觉。"
            ),
            args_schema=DescribeTableInput,
        ),
        StructuredTool.from_function(
            func=run_sql,
            name="run_sql",
            description=(
                "在指定数据源上执行一条只读 SELECT。一次只能执行一条语句。"
                "默认只返回前若干行给你看；需要完整数据做后续分析时用 save_as 存成 CSV，"
                "再用 bash 里的 python 处理。"
            ),
            args_schema=RunSQLInput,
        ),
    ]


def _confirm(datasource: str, sql: str, purpose: str) -> bool:
    """SQL 执行前的人工确认。

    通过 LangGraph 的 interrupt 把图挂起，SQL 原文送到前端等用户点确认。
    这是 MVP 阶段默认开启的——它是让业务同学敢用这个东西的前提，
    也是模型写错 SQL 时唯一的兜底。
    """
    from langgraph.errors import GraphInterrupt
    from langgraph.types import interrupt

    try:
        answer = interrupt(
            {
                "type": "sql_confirmation",
                "datasource": datasource,
                "sql": sql,
                "purpose": purpose,
            }
        )
    except GraphInterrupt:
        # 图挂起的信号必须原样往上抛，不能吞掉。
        raise
    except RuntimeError:
        # 不在图执行上下文里（例如评测框架直接调工具），跳过确认。
        return True
    if isinstance(answer, dict):
        return bool(answer.get("approved", False))
    return bool(answer)


def _to_csv(columns: list[str], rows: list[tuple]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue()
