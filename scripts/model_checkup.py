#!/usr/bin/env python3
"""模型体检 —— 立项第 0 周就该跑的东西。

harness 的成败取决于三件官方文档不会告诉你的事，而这三件事换个模型就会变：

1. **长 loop 稳定性**：连续 20+ 轮工具调用会不会跑飞、会不会忘记原始目标
2. **并行工具调用**：支不支持，支持的话参数会不会是幻觉
3. **长上下文指令遵循**：上下文到 50k+ 之后，system prompt 里的规矩还听不听

跑法::

    python scripts/model_checkup.py --model qwen-max --rounds 25

结果直接决定两件事：``parallel_tool_calls`` 要不要开，以及
``compaction_trigger_tokens`` 该设多少。别跳过这一步就去调 prompt——
你会把模型的能力边界误判成自己 prompt 写得不好。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from agentbase.config import LLMConfig
from agentbase.llm import build_chat_model
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

MAGIC = "紫水晶-7391"


class CounterInput(BaseModel):
    n: int = Field(description="当前计数")


class LookupInput(BaseModel):
    key: str = Field(description="要查的键名")


def _tools() -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            func=lambda n: str(n + 1),
            name="increment",
            description="把传入的数字加一并返回",
            args_schema=CounterInput,
        ),
        StructuredTool.from_function(
            func=lambda key: MAGIC if key == "暗号" else "未找到",
            name="lookup",
            description="按键名查一个值",
            args_schema=LookupInput,
        ),
    ]


def check_long_loop(model, rounds: int) -> tuple[bool, str]:
    """让模型连续调用 increment 直到目标值，看它会不会中途跑飞。"""
    target = rounds
    messages = [
        SystemMessage(
            content=(
                f"你在做一个计数任务。反复调用 increment 工具，把数字从 0 加到 {target}。"
                f"每次只调用一次工具。数到 {target} 后回复「完成:{target}」，不要提前停。"
            )
        ),
        HumanMessage(content="开始"),
    ]
    calls = 0
    for _ in range(rounds * 2 + 5):
        resp = model.invoke(messages)
        messages.append(resp)
        if not resp.tool_calls:
            ok = f"完成:{target}" in (resp.content or "")
            return ok, f"{calls} 轮工具调用后停止，最终回复: {str(resp.content)[:80]!r}"
        for call in resp.tool_calls:
            calls += 1
            args = call.get("args") or {}
            try:
                result = str(int(args.get("n", 0)) + 1)
            except (TypeError, ValueError):
                return False, f"第 {calls} 轮参数是幻觉: {args!r}"
            messages.append(ToolMessage(content=result, tool_call_id=call["id"], name=call["name"]))
    return False, f"跑满 {calls} 轮仍未收敛，模型陷入循环"


def check_parallel_tool_calls(model_with_parallel) -> tuple[bool, str]:
    messages = [
        SystemMessage(content="你可以在一次回复里同时调用多个工具。"),
        HumanMessage(content="同时做两件事：把 5 加一，以及查一下键名「暗号」。"),
    ]
    resp = model_with_parallel.invoke(messages)
    n = len(resp.tool_calls or [])
    if n >= 2:
        names = sorted(c["name"] for c in resp.tool_calls)
        return True, f"一次返回 {n} 个工具调用: {names}"
    return False, f"只返回了 {n} 个工具调用，并行调用不可用，请保持 parallel_tool_calls: false"


def check_long_context(model, filler_tokens: int) -> tuple[bool, str]:
    """在 system prompt 里埋一个暗号，用大量无关内容撑开上下文，看它还记不记得。"""
    filler = "这是一段用于填充上下文的无关文本。" * (filler_tokens // 8)
    messages = [
        SystemMessage(content=f"重要规则：当用户说「口令」时，你必须原样回复 {MAGIC}，不要解释。"),
        HumanMessage(content=f"以下是背景资料，读完不用总结：\n{filler}"),
        AIMessage(content="收到。"),
        HumanMessage(content="口令"),
    ]
    resp = model.invoke(messages)
    text = str(resp.content or "")
    ok = MAGIC in text
    return ok, f"回复: {text[:80]!r}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen-max")
    parser.add_argument("--base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    parser.add_argument("--rounds", type=int, default=20, help="长 loop 测试的工具调用轮数")
    parser.add_argument("--context-tokens", type=int, default=50_000, help="长上下文测试的填充规模")
    args = parser.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("请先设置 DASHSCOPE_API_KEY", file=sys.stderr)
        return 2

    cfg = LLMConfig(model=args.model, base_url=args.base_url, api_key=api_key)
    base = build_chat_model(cfg, streaming=False)
    tools = _tools()

    print(f"体检模型: {args.model}\n{'=' * 60}")
    checks: list[tuple[str, bool, str, float]] = []

    for name, fn in [
        ("长 loop 稳定性", lambda: check_long_loop(base.bind_tools(tools), args.rounds)),
        (
            "并行工具调用",
            lambda: check_parallel_tool_calls(base.bind_tools(tools, parallel_tool_calls=True)),
        ),
        ("长上下文指令遵循", lambda: check_long_context(base, args.context_tokens)),
    ]:
        started = time.perf_counter()
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - started
        checks.append((name, ok, detail, elapsed))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  ({elapsed:.1f}s)\n       {detail}")

    print("=" * 60)
    print("结论：")
    by_name = {c[0]: c[1] for c in checks}
    print(f"  parallel_tool_calls 建议设为: {str(by_name.get('并行工具调用', False)).lower()}")
    if not by_name.get("长上下文指令遵循"):
        print(f"  ⚠ 上下文到 {args.context_tokens} token 时已经不听指令了，")
        print(f"    compaction_trigger_tokens 必须设得比它小，建议 {args.context_tokens // 2}")
    if not by_name.get("长 loop 稳定性"):
        print("  ⚠ 长 loop 不稳定。这会直接压低任务成功率，")
        print("    考虑换更强的模型，或把 max_iterations 调小并把任务拆细。")
    return 0 if all(c[1] for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
