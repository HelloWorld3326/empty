"""从训练日志解析进度 — 与 trainer 的 stdout 标记协议对齐。

SkillOpt trainer 打印的事实标准标记（skillopt/engine/trainer.py）：
  [1/6 ROLLOUT] … [6/6 EVALUATE]   六阶段
  [EPOCH i/N]  [STEP g/T]           轮次/步进
  [SLOW UPDATE] / [META SKILL]      epoch 边界阶段
解析逻辑移植自 skillopt_webui/app.py::TrainingManager._parse_stage，改为
无状态函数：对日志尾部整体扫描取最新值，适配"按需解析"而非常驻线程。
"""
from __future__ import annotations

import re
from pathlib import Path

STAGES = ["Rollout", "Reflect", "Aggregate", "Select", "Update", "Gate"]

_STAGE_RE = re.compile(r"\[([1-6])/6\s", re.IGNORECASE)
_EPOCH_RE = re.compile(r"\[EPOCH\s+(\d+)\s*/\s*(\d+)\]", re.IGNORECASE)
_STEP_RE = re.compile(r"\[STEP\s+(\d+)\s*/\s*(\d+)\]", re.IGNORECASE)

TAIL_BYTES = 256 * 1024


def parse_progress(log_path: Path) -> dict:
    """扫描日志尾部，返回最新进度快照。"""
    result = {
        "stage_index": 0,          # 0 = 未开始，1-6 对应六阶段
        "stage_name": "",
        "epoch": 0,
        "total_epochs": 0,
        "step": 0,
        "total_steps": 0,
        "percent": 0.0,
    }
    if not log_path.is_file():
        return result

    with open(log_path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        text = f.read().decode("utf-8", errors="replace")

    for match in _STAGE_RE.finditer(text):
        result["stage_index"] = int(match.group(1))
    lower = text.lower()
    if result["stage_index"]:
        result["stage_name"] = STAGES[result["stage_index"] - 1]
    # epoch 边界阶段覆盖显示名（与原 Gradio 版一致）
    last_slow = lower.rfind("slow update")
    last_meta = lower.rfind("meta skill")
    last_stage_pos = 0
    for match in _STAGE_RE.finditer(text):
        last_stage_pos = match.start()
    if last_slow > last_stage_pos:
        result["stage_name"] = "Slow Update"
    if last_meta > max(last_stage_pos, last_slow):
        result["stage_name"] = "Meta Skill"

    for match in _EPOCH_RE.finditer(text):
        result["epoch"] = int(match.group(1))
        result["total_epochs"] = int(match.group(2))
    for match in _STEP_RE.finditer(text):
        result["step"] = int(match.group(1))
        result["total_steps"] = int(match.group(2))

    if result["total_steps"] > 0:
        result["percent"] = round(100.0 * result["step"] / result["total_steps"], 1)
    return result


def read_log_chunk(log_path: Path, offset: int, max_bytes: int = 512 * 1024) -> dict:
    """从 offset 起增量读取日志，返回内容与新 offset（供轮询接口使用）。"""
    if not log_path.is_file():
        return {"content": "", "next_offset": 0, "size": 0}
    size = log_path.stat().st_size
    offset = max(0, min(offset, size))
    with open(log_path, "rb") as f:
        f.seek(offset)
        data = f.read(max_bytes)
    return {
        "content": data.decode("utf-8", errors="replace"),
        "next_offset": offset + len(data),
        "size": size,
    }
