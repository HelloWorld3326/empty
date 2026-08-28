"""对话网关配置。所有密钥只存在于服务端，前端拿不到。"""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: str = "false") -> bool:
    return _env(name, default).lower() == "true"


@dataclass
class Settings:
    # ---- MaxKB ----
    # native：走 MaxKB 原生对话接口，能拿到 chat_id（需要查客户数据时必须用这个）
    # openai：走 OpenAI 兼容接口，适合纯知识问答
    maxkb_mode: str = field(default_factory=lambda: _env("MAXKB_MODE", "native"))
    maxkb_base_url: str = field(default_factory=lambda: _env("MAXKB_BASE_URL", "http://127.0.0.1:8080"))
    maxkb_api_key: str = field(default_factory=lambda: _env("MAXKB_API_KEY"))
    maxkb_app_id: str = field(default_factory=lambda: _env("MAXKB_APP_ID"))
    maxkb_timeout: float = field(default_factory=lambda: float(_env("MAXKB_TIMEOUT", "120")))
    # 不同版本接口路径不一样，以你部署版本自带的 API 文档为准，这里可直接改环境变量适配
    maxkb_path_profile: str = field(default_factory=lambda: _env("MAXKB_PATH_PROFILE", "/api/application/profile"))
    maxkb_path_open: str = field(default_factory=lambda: _env("MAXKB_PATH_OPEN", "/api/application/{app_id}/chat/open"))
    maxkb_path_message: str = field(default_factory=lambda: _env("MAXKB_PATH_MESSAGE", "/api/application/chat_message/{chat_id}"))
    maxkb_path_openai: str = field(default_factory=lambda: _env("MAXKB_PATH_OPENAI", "/v1/chat/completions"))

    # ---- 业务工具 API ----
    tool_api_base_url: str = field(default_factory=lambda: _env("TOOL_API_BASE_URL", "http://127.0.0.1:8100"))
    tool_api_service_key: str = field(default_factory=lambda: _env("TOOL_API_SERVICE_KEY"))
    tool_token_secret: str = field(default_factory=lambda: _env("TOOL_TOKEN_SECRET"))
    tool_token_audience: str = field(default_factory=lambda: _env("TOOL_TOKEN_AUDIENCE", "maxkb-tool-api"))
    tool_token_ttl: int = field(default_factory=lambda: int(_env("TOOL_TOKEN_TTL", "1800")))

    # ---- 客户身份来源 ----
    # debug：直接读 X-Debug-Customer-Id，仅本地联调用
    # jwt：校验客户360 签发的 JWT
    # introspect：调用客户360 的校验接口
    auth_mode: str = field(default_factory=lambda: _env("AUTH_MODE", "debug"))
    auth_jwt_secret: str = field(default_factory=lambda: _env("AUTH_JWT_SECRET"))
    auth_jwt_algorithms: str = field(default_factory=lambda: _env("AUTH_JWT_ALGORITHMS", "HS256"))
    auth_jwt_audience: str = field(default_factory=lambda: _env("AUTH_JWT_AUDIENCE"))
    auth_jwt_customer_claim: str = field(default_factory=lambda: _env("AUTH_JWT_CUSTOMER_CLAIM", "customer_id"))
    auth_introspect_url: str = field(default_factory=lambda: _env("AUTH_INTROSPECT_URL"))

    # ---- 防护 ----
    rate_limit_per_minute: int = field(default_factory=lambda: int(_env("RATE_LIMIT_PER_MINUTE", "20")))
    max_question_chars: int = field(default_factory=lambda: int(_env("MAX_QUESTION_CHARS", "1000")))
    mask_user_input: bool = field(default_factory=lambda: _bool("MASK_USER_INPUT", "true"))
    cors_origins: list[str] = field(default_factory=lambda: [o for o in _env("CORS_ORIGINS").split(",") if o])
    audit_log_path: str = field(default_factory=lambda: _env("AUDIT_LOG_PATH", "logs/audit.jsonl"))

    def validate(self) -> None:
        if self.maxkb_mode not in {"native", "openai"}:
            raise RuntimeError(f"MAXKB_MODE 取值非法: {self.maxkb_mode}")
        if not self.maxkb_api_key:
            raise RuntimeError("必须设置 MAXKB_API_KEY")
        if not self.tool_api_service_key:
            raise RuntimeError("必须设置 TOOL_API_SERVICE_KEY（与 tool-api 一致）")
        if self.auth_mode not in {"debug", "jwt", "introspect"}:
            raise RuntimeError(f"AUTH_MODE 取值非法: {self.auth_mode}")
        if self.auth_mode == "jwt" and not self.auth_jwt_secret:
            raise RuntimeError("AUTH_MODE=jwt 时必须设置 AUTH_JWT_SECRET")
        if self.auth_mode == "introspect" and not self.auth_introspect_url:
            raise RuntimeError("AUTH_MODE=introspect 时必须设置 AUTH_INTROSPECT_URL")


settings = Settings()
