"""测试夹具：用 SQLite 造一个微型业务库，让整条链路可以离线跑通。

用 SQLite 是刻意的——评测和单测不该依赖能连上生产 PG/MySQL。
数据接入层走的是 SQLAlchemy，方言差异由它吸收。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentbase.config import (  # noqa: E402
    AgentConfig,
    Config,
    DataSourceConfig,
    RoleConfig,
    SandboxConfig,
    SkillsConfig,
)
from agentbase.datasources.introspect import SchemaCache  # noqa: E402
from agentbase.datasources.registry import DataSourceRegistry  # noqa: E402
from agentbase.datasources.retriever import build_retriever  # noqa: E402
from agentbase.sandbox.local import LocalSandboxProvider  # noqa: E402
from agentbase.skillsys.loader import SkillRegistry  # noqa: E402
from agentbase.tools.context import RunContext  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "shop.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE orders ("
                "  order_id INTEGER PRIMARY KEY,"
                "  customer_id INTEGER NOT NULL,"
                "  amount INTEGER NOT NULL,"
                "  status TEXT NOT NULL,"
                "  is_test INTEGER NOT NULL DEFAULT 0,"
                "  created_at TEXT NOT NULL)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE customers ("
                "  customer_id INTEGER PRIMARY KEY,"
                "  name TEXT NOT NULL,"
                "  region TEXT NOT NULL)"
            )
        )
        conn.execute(
            text("INSERT INTO customers VALUES (1,'甲公司','华东'),(2,'乙公司','华北')")
        )
        conn.execute(
            text(
                "INSERT INTO orders VALUES"
                " (1,1,10000,'paid',0,'2026-01-01'),"
                " (2,1,25000,'completed',0,'2026-01-02'),"
                " (3,2,5000,'cancelled',0,'2026-01-03'),"
                " (4,2,99900,'paid',1,'2026-01-04')"
            )
        )
    engine.dispose()
    return path


@pytest.fixture
def config(db_path: Path) -> Config:
    return Config(
        agent=AgentConfig(require_sql_confirmation=False),
        sandbox=SandboxConfig(provider="local"),
        skills=SkillsConfig(paths=[str(REPO_ROOT / "skills")]),
        checkpoint_dsn=f"sqlite:///{db_path.parent / 'checkpoints.db'}",
        datasources=[
            DataSourceConfig(
                name="shop",
                dsn=f"sqlite:///{db_path}",
                description="测试用商城库",
                max_rows=1000,
                max_rows_to_model=10,
            )
        ],
        roles=[
            RoleConfig(name="分析师", datasources=["shop"], table_allowlist=["*"]),
            RoleConfig(name="销售", datasources=["shop"], table_allowlist=["customers"]),
        ],
    )


@pytest.fixture
def registry(config: Config) -> DataSourceRegistry:
    reg = DataSourceRegistry(config)
    yield reg
    reg.dispose()


@pytest.fixture
def schema_cache(registry: DataSourceRegistry, tmp_path: Path) -> SchemaCache:
    cache = SchemaCache(tmp_path / "schema.json")
    cache.refresh(registry)
    return cache


@pytest.fixture
def run_context(
    config: Config, registry: DataSourceRegistry, schema_cache: SchemaCache, tmp_path: Path
) -> RunContext:
    skills = SkillRegistry([str(REPO_ROOT / "skills")])
    skills.reload()
    provider = LocalSandboxProvider(base_dir=str(tmp_path / "sandboxes"))
    ctx = RunContext(
        config=config,
        datasources=registry,
        schema_cache=schema_cache,
        retriever=build_retriever(schema_cache),
        skills=skills,
        sandbox=provider.acquire("test-session"),
        role=config.role("分析师"),
        session_id="test-session",
        require_sql_confirmation=False,
    )
    yield ctx
    provider.release("test-session")
