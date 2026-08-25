#!/usr/bin/env python3
"""跑回归评测集。

    # 召回评测：不调模型，秒级跑完，改召回策略时先跑这个
    python scripts/run_evals.py --cases evals/cases.yaml --retrieval-only

    # 端到端：真调模型，比对执行结果
    python scripts/run_evals.py --cases evals/cases.yaml

准确率数字是这个项目唯一的方向盘。每次改 prompt、换模型、动召回策略之后
都要跑一遍并把数字记下来，否则你无法判断改动是变好还是变坏。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from agentbase.evalkit.cases import EvalCase, load_cases
from agentbase.evalkit.runner import evaluate_end_to_end, evaluate_retrieval
from agentbase.runtime import Runtime
from langchain_core.messages import HumanMessage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="evals/cases.yaml")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--top-k", type=int, default=20)
    args = parser.parse_args()

    from agentbase.config import load_config

    config = load_config(args.config)
    # 评测跑批不可能有人一条条点确认。
    config.agent.require_sql_confirmation = False

    runtime = Runtime(config)
    runtime.start()
    try:
        cases = load_cases(args.cases)
        print(f"载入 {len(cases)} 条用例\n")

        report = evaluate_retrieval(cases, runtime.retriever, runtime.datasources, top_k=args.top_k)
        print(f"--- 召回评测 (top-{args.top_k}) ---")
        print(report.render())

        if args.retrieval_only:
            return 0 if report.accuracy == 1.0 else 1

        def run_agent(case: EvalCase) -> tuple[str, list[str]]:
            session = f"eval-{case.id}"
            ctx = runtime.make_context(session, case.role)
            ctx.require_sql_confirmation = False
            graph = runtime.compile_graph(ctx)
            try:
                state = graph.invoke(
                    {"messages": [HumanMessage(content=case.question)], "iterations": 0},
                    config={"configurable": {"thread_id": session}},
                )
                answer = str(state["messages"][-1].content)
                return answer, list(ctx.executed_sql)
            finally:
                runtime.end_session(session)

        print("\n--- 端到端评测 ---")
        e2e = evaluate_end_to_end(cases, runtime.datasources, run_agent)
        print(e2e.render())
        return 0 if e2e.accuracy >= 0.8 else 1
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
