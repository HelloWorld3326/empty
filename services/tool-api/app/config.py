"""业务工具 API 的配置。全部通过环境变量注入，不在代码里写死任何密钥。"""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    # MaxKB 工作流的 HTTP 节点必须带上这个头，防止工具 API 被随意调用
    service_key: str = field(default_factory=lambda: _env("TOOL_API_SERVICE_KEY"))
    # 与 chat-gateway 共享的签名密钥，用于校验短期 tool_token（可选方案）
    token_secret: str = field(default_factory=lambda: _env("TOOL_TOKEN_SECRET"))
    token_audience: str = field(default_factory=lambda: _env("TOOL_TOKEN_AUDIENCE", "maxkb-tool-api"))
    # 数据源：mock（内置示例数据）/ db（数据库）/ http（转调你们已有的接口）
    data_source: str = field(default_factory=lambda: _env("TOOL_DATA_SOURCE", "mock"))
    db_dsn: str = field(default_factory=lambda: _env("TOOL_DB_DSN"))
    upstream_base_url: str = field(default_factory=lambda: _env("TOOL_UPSTREAM_BASE_URL"))
    upstream_token: str = field(default_factory=lambda: _env("TOOL_UPSTREAM_TOKEN"))
    upstream_timeout: float = field(default_factory=lambda: float(_env("TOOL_UPSTREAM_TIMEOUT", "8")))
    # 返回给大模型之前是否脱敏（数据要出内网给云端模型时务必保持开启）
    mask_pii: bool = field(default_factory=lambda: _env("TOOL_MASK_PII", "true").lower() == "true")

    def validate(self) -> None:
        if not self.service_key:
            raise RuntimeError("必须设置 TOOL_API_SERVICE_KEY")
        if self.data_source not in {"mock", "db", "http"}:
            raise RuntimeError(f"TOOL_DATA_SOURCE 取值非法: {self.data_source}")
        if self.data_source == "db" and not self.db_dsn:
            raise RuntimeError("TOOL_DATA_SOURCE=db 时必须设置 TOOL_DB_DSN")
        if self.data_source == "http" and not self.upstream_base_url:
            raise RuntimeError("TOOL_DATA_SOURCE=http 时必须设置 TOOL_UPSTREAM_BASE_URL")


settings = Settings()
