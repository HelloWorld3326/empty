"""一次运行所需的全部依赖，打包成一个对象传给工具层。

工具函数都是闭包在这个上下文上的，好处是工具本身没有全局状态，
评测框架可以直接构造一个 RunContext 跑单测，不用起网关。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Config, RoleConfig
from ..datasources.introspect import SchemaCache
from ..datasources.registry import DataSourceRegistry
from ..datasources.retriever import SchemaRetriever
from ..sandbox.base import Sandbox
from ..skillsys.loader import SkillRegistry


@dataclass
class RunContext:
    config: Config
    datasources: DataSourceRegistry
    schema_cache: SchemaCache
    retriever: SchemaRetriever
    skills: SkillRegistry
    sandbox: Sandbox
    role: RoleConfig | None = None
    session_id: str = "default"
    # 本次运行已执行的 SQL，用于评测和审计。
    executed_sql: list[str] = field(default_factory=list)
    # 关掉后 run_sql 不再要求人工确认。评测跑批时会置为 False。
    require_sql_confirmation: bool = True
