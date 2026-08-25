"""评测集格式。

一条用例 = 一个真实问过的问题 + 一条人工确认过的正确 SQL。

**强烈建议从数据库的历史查询日志或现有 BI 报表里捞，不要凭空设计。**
捞出来的问题天然符合真实分布，设计出来的问题会系统性地偏简单，
让你对准确率产生错觉。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class EvalCase:
    id: str
    question: str
    datasource: str
    # 人工确认过的正确 SQL。评分比的是它的执行结果，不是 SQL 文本——
    # 同一个问题有无数种写法，比文本毫无意义。
    golden_sql: str
    role: str | None = None
    # 结果顺序是否重要。绝大多数聚合类问题不重要，默认忽略顺序。
    ordered: bool = False
    tags: list[str] = field(default_factory=list)
    note: str = ""


def load_cases(path: str | Path) -> list[EvalCase]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cases = raw.get("cases", raw if isinstance(raw, list) else [])
    return [EvalCase(**c) for c in cases]
