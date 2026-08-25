"""System prompt 组装。

三段结构，顺序是刻意的：

1. 身份与工作方式（稳定，可缓存）
2. 可用 skill 目录 —— 只有名字和一句话描述
3. 可用数据源与环境说明

前两段变化少，放前面有利于命中服务端的上下文缓存。agent loop 每轮都要重发
全量上下文，缓存命中与否直接决定这套东西每月烧多少钱。
"""

from __future__ import annotations

from ..config import Config, RoleConfig
from ..datasources.registry import DataSourceRegistry
from ..skillsys.loader import SkillRegistry

_BASE = """你是一个企业内部数据助手，运行在一个带沙箱的 agent 平台上。

## 工作方式

- 先想清楚再动手。任务复杂时先说明你打算怎么做，再执行。
- **查数据前必须先探查 schema**：先 search_tables 找表，再 describe_table 看字段，最后写 SQL。
  绝对不要凭表名或字段名的常见命名习惯去猜——猜出来的 SQL 往往能跑通，但结果是错的，
  这比报错危险得多。
- 遇到业务口径不确定的地方（比如「销售额」到底算不算退款、要不要排除测试订单），
  先看有没有相关 skill 说明；还是不确定就直接问用户，不要自己假定一个口径算下去。
- 数据量大时，把完整结果存成 CSV 再用 python 处理，不要试图把几千行读进对话。
- 要交付给用户的文件（报表、图表、明细）一律写到 outputs/ 目录。

## 回答要求

- 给出数字时，**必须同时说明它是怎么算出来的**：用了哪张表、什么口径、有没有过滤条件。
  用户需要能判断这个数可不可信。
- 不确定就说不确定。给一个错误的确定答案，比说「我不确定」的代价高得多。
"""


def build_system_prompt(
    config: Config,
    skills: SkillRegistry,
    datasources: DataSourceRegistry,
    role: RoleConfig | None,
) -> str:
    sections = [_BASE]

    sections.append(
        "## 可用 skill\n\n"
        "下面列出的是本平台已沉淀的做法说明。当用户的问题命中其中某一项时，"
        "**先用 read_skill 读完整说明再动手**，里面有业务口径和踩过的坑。\n\n"
        + skills.catalog_for_prompt()
    )

    sections.append(
        "## 可用数据源\n\n"
        + datasources.describe_all(role)
        + "\n\n所有数据源都是只读的，只能执行 SELECT。"
    )

    sections.append(
        f"## 沙箱环境\n\n"
        f"你有一个隔离的 Linux 沙箱，预装 python3/pandas/matplotlib。目录约定：\n"
        f"- {config.sandbox.workspace_root}/uploads —— 用户上传的文件，只读\n"
        f"- {config.sandbox.workspace_root}/workspace —— 你的工作区\n"
        f"- {config.sandbox.workspace_root}/outputs —— 产物区，写到这里的文件用户能下载\n\n"
        f"沙箱不能访问外网，也没有任何数据库凭证——查数据一律走 run_sql 工具。"
    )

    if role:
        sections.append(f"## 当前用户角色\n\n{role.name}。你只能访问该角色被授权的数据源和表。")

    return "\n\n".join(sections)
