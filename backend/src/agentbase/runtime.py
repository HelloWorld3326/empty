"""平台运行时装配 —— 把各层拼起来的唯一地方。

进程级共享：配置、数据源连接池、schema 缓存、skill 注册表、沙箱 provider。
会话级独立：沙箱实例、RunContext、编译好的图。
"""

from __future__ import annotations

import logging
from typing import Any

from .config import Config, RoleConfig, load_config
from .datasources.introspect import SchemaCache
from .datasources.registry import DataSourceRegistry
from .datasources.retriever import build_retriever
from .graph.agent import build_agent_graph, build_checkpointer
from .sandbox.base import SandboxProvider
from .sandbox.k8s import build_provider
from .skillsys.loader import SkillRegistry
from .tools.context import RunContext

logger = logging.getLogger(__name__)


class Runtime:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or load_config()
        self.datasources = DataSourceRegistry(self.config)
        self.schema_cache = SchemaCache()
        self.retriever = build_retriever(
            self.schema_cache,
            aliases={ds.name: ds.table_aliases for ds in self.config.datasources},
        )
        self.skills = SkillRegistry(
            self.config.skills.resolved_paths(self.config.base_dir),
            max_description_chars=self.config.skills.max_description_chars,
        )
        self.sandboxes: SandboxProvider = build_provider(self.config.sandbox)
        self.checkpointer: Any = None
        self._close_checkpointer: Any = None

    def start(self) -> None:
        count, errors = self.skills.reload()
        logger.info("已加载 %d 个 skill", count)
        for err in errors:
            logger.warning("skill 加载错误: %s", err)

        if not self.schema_cache.load():
            logger.info("schema 缓存为空，开始内省……")
            self.refresh_schema()

        self.checkpointer, self._close_checkpointer = build_checkpointer(self.config)

    def refresh_schema(self, names: list[str] | None = None) -> dict[str, int]:
        counts = self.schema_cache.refresh(self.datasources, names)
        if hasattr(self.retriever, "invalidate"):
            self.retriever.invalidate()
        logger.info("schema 内省完成: %s", counts)
        return counts

    def reload_skills(self) -> tuple[int, list[str]]:
        return self.skills.reload()

    def make_context(self, session_id: str, role_name: str | None = None) -> RunContext:
        role: RoleConfig | None = self.config.role(role_name) if role_name else None
        if role_name and role is None:
            raise KeyError(f"未知角色: {role_name}")
        return RunContext(
            config=self.config,
            datasources=self.datasources,
            schema_cache=self.schema_cache,
            retriever=self.retriever,
            skills=self.skills,
            sandbox=self.sandboxes.acquire(session_id),
            role=role,
            session_id=session_id,
            require_sql_confirmation=self.config.agent.require_sql_confirmation,
        )

    def compile_graph(self, ctx: RunContext) -> Any:
        return build_agent_graph(ctx, self.config).compile(checkpointer=self.checkpointer)

    def end_session(self, session_id: str) -> None:
        self.sandboxes.release(session_id)

    def shutdown(self) -> None:
        self.datasources.dispose()
        if self._close_checkpointer is not None:
            self._close_checkpointer()
            self._close_checkpointer = None
