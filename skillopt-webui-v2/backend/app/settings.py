"""服务配置 — 全部通过环境变量控制，便于容器化部署。"""
from __future__ import annotations

import os
from pathlib import Path


class Settings:
    """运行时配置。

    环境变量：
      SKILLOPT_REPO                SkillOpt 源码仓库路径（含 scripts/train.py、configs/），默认 /opt/skillopt
      SKILLOPT_DATA_DIR            任务与密钥数据目录（挂卷持久化），默认 <repo>/webui_data
      SKILLOPT_MAX_CONCURRENT_JOBS 同时运行的训练任务数上限，默认 1，超出排队
      SKILLOPT_AUTH_MODE           none | jwt，默认 none（内网部署、仅经 nexent 网关访问）
      SKILLOPT_JWT_SECRET          jwt 模式下的 HS256 密钥（与 nexent 的 Supabase JWT secret 一致即可打通登录）
      SKILLOPT_PYTHON              运行 scripts/train.py 的 Python 解释器，默认当前解释器
    """

    def __init__(self, env: dict | None = None):
        env = env if env is not None else os.environ
        self.repo_root = Path(env.get("SKILLOPT_REPO", "/opt/skillopt")).resolve()
        self.data_dir = Path(
            env.get("SKILLOPT_DATA_DIR", str(self.repo_root / "webui_data"))
        ).resolve()
        self.max_concurrent_jobs = int(env.get("SKILLOPT_MAX_CONCURRENT_JOBS", "1"))
        self.auth_mode = env.get("SKILLOPT_AUTH_MODE", "none").strip().lower()
        self.jwt_secret = env.get("SKILLOPT_JWT_SECRET", "")
        self.jwt_algorithms = [
            a.strip()
            for a in env.get("SKILLOPT_JWT_ALGORITHMS", "HS256").split(",")
            if a.strip()
        ]
        self.python_bin = env.get("SKILLOPT_PYTHON", "")

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def secrets_file(self) -> Path:
        return self.data_dir / "secrets" / "webui.env"

    def ensure_dirs(self) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.secrets_file.parent.mkdir(parents=True, exist_ok=True)
