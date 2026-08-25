"""回归评测。

两个层次，分开跑：

**召回评测**（``evaluate_retrieval``）不需要调模型，秒级跑完，零成本。
它衡量的是「正确的表有没有出现在候选里」——如果这一步就丢了，
后面模型再强也没用。调 schema 召回策略时跑这个。

**端到端评测**（``evaluate_end_to_end``）真调模型跑完整 agent，比对执行结果。
慢且花钱，但这是唯一能回答「改这行 prompt 到底有没有用」的东西。

评分口径是**执行结果比对**，不是 SQL 文本比对。同一个问题有无数种正确写法，
比文本只会得到一个毫无意义的低分。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..datasources.registry import DataSourceRegistry
from ..datasources.retriever import SchemaRetriever
from .cases import EvalCase

logger = logging.getLogger(__name__)


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    reason: str = ""
    actual_sql: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


@dataclass
class EvalReport:
    results: list[CaseResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def render(self) -> str:
        lines = [f"准确率: {self.passed}/{self.total} = {self.accuracy:.1%}", ""]
        for r in self.results:
            mark = "PASS" if r.passed else "FAIL"
            lines.append(f"[{mark}] {r.case_id}  {r.reason}")
        return "\n".join(lines)


def _normalize(rows: list[tuple], *, ordered: bool) -> Any:
    # 数值统一成字符串比较，避开 Decimal/float/int 的类型差异。
    norm = [tuple("" if v is None else str(v) for v in row) for row in rows]
    return norm if ordered else sorted(norm)


def evaluate_retrieval(
    cases: list[EvalCase],
    retriever: SchemaRetriever,
    registry: DataSourceRegistry,
    *,
    top_k: int = 20,
) -> EvalReport:
    """召回评测：golden SQL 用到的表，是否都在候选 top-k 里。

    不调模型，跑一次几秒钟。改召回策略时先跑这个，涨了再去跑端到端。
    """
    from ..datasources.guard import guard_sql

    results = []
    for case in cases:
        ds = registry.config_for(case.datasource)
        try:
            guarded = guard_sql(case.golden_sql, dsn=ds.dsn, max_rows=ds.max_rows)
        except Exception as exc:
            results.append(CaseResult(case.id, False, f"golden SQL 无法解析: {exc}"))
            continue

        needed = {t.split(".")[-1].lower() for t in guarded.tables}
        got = {
            t.name.lower()
            for t in retriever.search(case.question, datasource=case.datasource, limit=top_k)
        }
        missing = needed - got
        results.append(
            CaseResult(
                case.id,
                not missing,
                "" if not missing else f"top-{top_k} 未召回: {', '.join(sorted(missing))}",
            )
        )
    return EvalReport(results)


def evaluate_end_to_end(
    cases: list[EvalCase],
    registry: DataSourceRegistry,
    run_agent: Callable[[EvalCase], tuple[str, list[str]]],
) -> EvalReport:
    """端到端评测。

    ``run_agent`` 接收一条用例，返回 (最终回答文本, agent 实际执行过的 SQL 列表)。
    评分只看最后一条 SQL 的执行结果是否与 golden 一致——中间的探查性查询不计入。
    """
    results: list[CaseResult] = []
    for case in cases:
        try:
            _answer, executed = run_agent(case)
        except Exception as exc:
            results.append(
                CaseResult(case.id, False, f"agent 运行异常: {type(exc).__name__}: {exc}")
            )
            continue

        if not executed:
            results.append(CaseResult(case.id, False, "agent 没有执行任何 SQL"))
            continue

        try:
            expected = registry.execute(case.datasource, case.golden_sql)
            actual = registry.execute(case.datasource, executed[-1])
        except Exception as exc:
            results.append(
                CaseResult(case.id, False, f"结果比对时执行失败: {exc}", actual_sql=executed)
            )
            continue

        same = _normalize(expected.rows, ordered=case.ordered) == _normalize(
            actual.rows, ordered=case.ordered
        )
        results.append(
            CaseResult(
                case.id,
                same,
                ""
                if same
                else f"结果不一致: 期望 {expected.row_count} 行，实际 {actual.row_count} 行",
                actual_sql=executed,
                elapsed_ms=actual.elapsed_ms,
            )
        )
    return EvalReport(results)
