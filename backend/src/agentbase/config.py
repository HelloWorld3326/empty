"""配置加载。

单一入口：config.yaml + 环境变量。yaml 里的 ``${VAR}`` 会用环境变量展开，
所以密钥永远只出现在 .env / k8s Secret 里，不进代码仓库。
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand(value: Any) -> Any:
    """递归展开 ${VAR} 与 ${VAR:-default}。找不到且无默认值时报错，不静默留空。"""
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            got = os.environ.get(name)
            if got is None:
                if default is None:
                    raise RuntimeError(f"配置引用了未设置的环境变量 {name}")
                return default
            return got

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


class LLMConfig(BaseModel):
    """默认走阿里云百炼的 OpenAI 兼容端点，换厂商只改 base_url 和 model。"""

    model: str = "qwen-max"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str = Field(default="", repr=False)
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_seconds: int = 120
    # 国内模型对并行 tool call 的支持参差不齐，默认关掉，体检通过后再开。
    parallel_tool_calls: bool = False


class AgentConfig(BaseModel):
    max_iterations: int = 30
    # 上下文超过这个 token 数就触发压缩。agent loop 每轮重发全量上下文，
    # 不压缩的话 token 消耗随轮次平方级增长。
    compaction_trigger_tokens: int = 48_000
    compaction_keep_recent_messages: int = 6
    # SQL 执行前是否需要用户确认。MVP 默认开——这是让人敢用的前提。
    require_sql_confirmation: bool = True


class SandboxConfig(BaseModel):
    provider: Literal["local", "k8s"] = "local"
    image: str = "agentbase/sandbox:latest"
    namespace: str = "agentbase"
    idle_timeout_seconds: int = 900
    workspace_root: str = "/mnt/user-data"
    cpu_limit: str = "1"
    memory_limit: str = "2Gi"
    # 沙箱默认禁止一切出网，只放行白名单。agent 手里有 bash，
    # 一次提示注入就能把东西 curl 出去。
    egress_allowlist: list[str] = Field(default_factory=list)


class DataSourceConfig(BaseModel):
    name: str
    dsn: str = Field(repr=False)
    description: str = ""
    # 每个源必须绑只读账号；这里再做一层应用层拦截，双保险。
    readonly: bool = True
    statement_timeout_seconds: int = 30
    max_rows: int = 5000
    # 回传给模型的行数上限。明细走沙箱文件，不进 prompt。
    max_rows_to_model: int = 50
    include_schemas: list[str] = Field(default_factory=list)
    exclude_tables: list[str] = Field(default_factory=list)
    # 业务别名：``{表名: [别名, ...]}``。中文提问打英文表名时，
    # 这是让 schema 召回能用起来的最省事的办法，比上向量库见效快得多。
    table_aliases: dict[str, list[str]] = Field(default_factory=dict)


class RoleConfig(BaseModel):
    """角色 → 可访问的数据源/表白名单（权限 b 档）。

    行级权限没实现，但 run_sql 里留了 row_filter 钩子，
    需要升到 c 档时在这里加 ``row_filters`` 即可。
    """

    name: str
    datasources: list[str] = Field(default_factory=list)
    # 支持 "schema.*" 通配。留空表示该数据源下全部可见。
    table_allowlist: list[str] = Field(default_factory=list)


class SkillsConfig(BaseModel):
    paths: list[str] = Field(default_factory=lambda: ["skills"])
    # 只有 name+description 进 system prompt，全文靠 read_skill 按需拉。
    max_description_chars: int = 200

    def resolved_paths(self, base_dir: Path | None) -> list[str]:
        """相对路径按 config.yaml 所在目录解析。

        按进程 CWD 解析是个隐蔽的坑：本地跑在仓库根目录能找到 skill，
        容器里 workdir 一变就静默加载不到任何 skill，而平台照常启动，
        只是所有 agent 都变笨了——这种故障很难查。
        """
        if base_dir is None:
            return list(self.paths)
        return [str(p if (p := Path(raw)).is_absolute() else base_dir / raw) for raw in self.paths]


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    datasources: list[DataSourceConfig] = Field(default_factory=list)
    roles: list[RoleConfig] = Field(default_factory=list)
    checkpoint_dsn: str = "sqlite:///./agentbase.db"
    # config.yaml 所在目录，用来解析配置里的相对路径。程序化构造时为 None。
    base_dir: Path | None = Field(default=None, exclude=True)

    def datasource(self, name: str) -> DataSourceConfig:
        for ds in self.datasources:
            if ds.name == name:
                return ds
        raise KeyError(f"未知数据源: {name}")

    def role(self, name: str) -> RoleConfig | None:
        for r in self.roles:
            if r.name == name:
                return r
        return None


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path or os.environ.get("AGENTBASE_CONFIG", "config.yaml"))
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件 {path}，可从 config.example.yaml 复制一份")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = Config.model_validate(_expand(raw))
    config.base_dir = path.resolve().parent
    return config


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()


def reset_config_cache() -> None:
    get_config.cache_clear()
