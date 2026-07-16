"""对 SkillOpt 仓库的适配层 — 训练引擎零修改。

通过文件路径直接加载 ``<repo>/skillopt/config.py``（该模块仅依赖 PyYAML 与标准库），
避免 import 整个 skillopt 包（那会拉起训练侧的重依赖）。其余函数移植自
``skillopt_webui/app.py``（原 Gradio 版）：build_training_env / validate_training_config /
discover_configs，保持与原实现相同的行为契约。
"""
from __future__ import annotations

import glob
import importlib.util
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

_CONFIG_MODULE_CACHE: dict[str, object] = {}


def load_config_module(repo_root: Path):
    """按路径加载 SkillOpt 的 config 模块（skillopt/config.py）。"""
    key = str(repo_root)
    if key in _CONFIG_MODULE_CACHE:
        return _CONFIG_MODULE_CACHE[key]
    cfg_py = repo_root / "skillopt" / "config.py"
    if not cfg_py.is_file():
        raise FileNotFoundError(
            f"SkillOpt repo not found or invalid: missing {cfg_py}. "
            "Set SKILLOPT_REPO to a SkillOpt checkout."
        )
    spec = importlib.util.spec_from_file_location(f"_skillopt_config_{abs(hash(key))}", cfg_py)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _CONFIG_MODULE_CACHE[key] = module
    return module


def discover_configs(repo_root: Path) -> list[str]:
    """列出 configs/ 下全部 benchmark 预设（排除 _base_），返回仓库相对路径。"""
    pattern = str(repo_root / "configs" / "**" / "*.yaml")
    paths = sorted(glob.glob(pattern, recursive=True))
    return [os.path.relpath(p, repo_root) for p in paths if "_base_" not in p]


def load_merged_config(repo_root: Path, rel_path: str, overrides: dict | None = None) -> dict:
    """加载合并后的结构化配置（解析 _base_ 继承并应用 overrides）。"""
    mod = load_config_module(repo_root)
    abs_path = _safe_config_path(repo_root, rel_path)
    cfg_options = _overrides_to_options(overrides or {})
    return mod.load_config(str(abs_path), cfg_options)


def flatten(repo_root: Path, cfg: dict) -> dict:
    mod = load_config_module(repo_root)
    return mod.flatten_config(cfg)


def _safe_config_path(repo_root: Path, rel_path: str) -> Path:
    """校验配置路径必须位于 <repo>/configs 内，防止路径穿越。"""
    abs_path = (repo_root / rel_path).resolve()
    configs_root = (repo_root / "configs").resolve()
    if not str(abs_path).startswith(str(configs_root) + os.sep):
        raise ValueError(f"config path escapes configs/: {rel_path}")
    if not abs_path.is_file():
        raise FileNotFoundError(f"config not found: {rel_path}")
    return abs_path


def _overrides_to_options(overrides: dict) -> list[str]:
    return [
        f"{key}={value}"
        for key, value in overrides.items()
        if value is not None and value != ""
    ]


# ─── 训练子进程环境（移植自 skillopt_webui/app.py） ──────────────────────────

def _load_env_file(path: Path, env: dict[str, str]) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("\"'")


def build_training_env(repo_root: Path, extra_env_files: list[Path] | None = None) -> dict[str, str]:
    """合并 os.environ + <repo>/.env + <repo>/.secrets/*.env + WebUI 管理的密钥文件。"""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    dot_env = repo_root / ".env"
    if dot_env.is_file():
        _load_env_file(dot_env, env)

    secrets_dir = repo_root / ".secrets"
    if secrets_dir.is_dir():
        for env_file in sorted(secrets_dir.glob("*.env")):
            _load_env_file(env_file, env)

    for extra in extra_env_files or []:
        if extra.is_file():
            _load_env_file(extra, env)

    # OPTIMIZER_* 缺省回填到共享的 AZURE_OPENAI_*（与原 Gradio 版一致）
    for suffix in (
        "ENDPOINT", "API_VERSION", "AUTH_MODE", "MANAGED_IDENTITY_CLIENT_ID",
        "AD_SCOPE", "API_KEY",
    ):
        base_key = f"AZURE_OPENAI_{suffix}"
        optimizer_key = f"OPTIMIZER_AZURE_OPENAI_{suffix}"
        if not env.get(base_key) and env.get(optimizer_key):
            env[base_key] = env[optimizer_key]
    return env


# ─── 启动前预检（移植自 skillopt_webui/app.py::validate_training_config） ────

def _can_connect_to_url(url: str, timeout: float = 0.5) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def validate_training_config(
    repo_root: Path,
    rel_path: str,
    overrides: dict,
    env: dict[str, str] | None = None,
) -> str | None:
    """返回可读的预检错误信息；None 表示可以启动训练。"""
    env = env or os.environ
    try:
        cfg = flatten(repo_root, load_merged_config(repo_root, rel_path, overrides))
    except Exception as exc:
        return f"Invalid config: {exc}"

    shared_endpoint = (
        cfg.get("azure_openai_endpoint")
        or cfg.get("azure_endpoint")
        or env.get("AZURE_OPENAI_ENDPOINT")
    )
    missing_openai_roles = []
    for role in ("optimizer", "target"):
        if cfg.get(f"{role}_backend") != "openai_chat":
            continue
        role_endpoint = (
            cfg.get(f"{role}_azure_openai_endpoint")
            or env.get(f"{role.upper()}_AZURE_OPENAI_ENDPOINT")
            or shared_endpoint
        )
        if not role_endpoint:
            missing_openai_roles.append(role)
    if missing_openai_roles:
        configured_backend = cfg.get("model_backend")
        detail = ""
        if configured_backend in {"qwen", "qwen_chat"}:
            detail = (
                " Note: model.backend is qwen, but explicit optimizer_backend/"
                "target_backend values are still openai_chat."
            )
        return (
            "Model backend is not ready: missing Azure/OpenAI-compatible endpoint "
            f"for {', '.join(missing_openai_roles)}. "
            "Set model.azure_openai_endpoint (or AZURE_OPENAI_ENDPOINT), or change "
            f"the role backends to the backend you intend to use.{detail}"
        )

    qwen_failures = []
    qwen_shared = (
        cfg.get("qwen_chat_base_url")
        or env.get("QWEN_CHAT_BASE_URL")
        or "http://localhost:8000/v1"
    )
    for role in ("optimizer", "target"):
        if cfg.get(f"{role}_backend") != "qwen_chat":
            continue
        base_url = (
            cfg.get(f"{role}_qwen_chat_base_url")
            or env.get(f"{role.upper()}_QWEN_CHAT_BASE_URL")
            or qwen_shared
        )
        if not _can_connect_to_url(str(base_url)):
            qwen_failures.append(f"{role}={base_url}")
    if qwen_failures:
        return (
            "Model backend is not ready: cannot connect to qwen_chat endpoint "
            f"for {', '.join(qwen_failures)}. "
            "Start your OpenAI-compatible Qwen/vLLM server, or set "
            "model.qwen_chat_base_url / OPTIMIZER_QWEN_CHAT_BASE_URL / "
            "TARGET_QWEN_CHAT_BASE_URL to the correct URL."
        )
    return None


# ─── 配置脱敏 ────────────────────────────────────────────────────────────────

_SECRET_MARKERS = ("api_key", "secret", "token", "password")


def redact_config(cfg: dict) -> dict:
    """递归脱敏：凡 key 含 api_key/secret/token/password 且有值的字段替换为掩码。"""
    result = {}
    for key, value in cfg.items():
        if isinstance(value, dict):
            result[key] = redact_config(value)
        elif any(m in key.lower() for m in _SECRET_MARKERS) and value:
            result[key] = "******"
        else:
            result[key] = value
    return result
