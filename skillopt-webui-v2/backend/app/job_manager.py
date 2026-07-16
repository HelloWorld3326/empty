"""多任务训练管理器 — 把原 Gradio 版的单任务 TrainingManager 升级为：

  * 多任务 + 并发上限（超出排队，FIFO）
  * 每任务独立目录，元数据(job.json)/日志(train.log)/产物(out/) 全部落盘，
    服务重启不丢状态
  * 停止 = killpg 整个进程组；继续训练 = 相同 out_root 重新拉起
    （SkillOpt trainer 检测到 out_root 下的 runtime_state.json 自动断点续训）

与 SkillOpt 的调用契约保持与原 Gradio 版完全一致：
  python scripts/train.py --config <rel> --cfg-options k=v ...   (cwd=repo)
训练引擎零修改。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .settings import Settings
from . import skillopt_compat as compat

# 任务状态机：
#   queued -> running -> succeeded | failed | stopped
#   failed/stopped --resume--> queued -> running -> ...
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_STOPPING = "stopping"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"

FINISHED = {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_STOPPED}


@dataclass
class Job:
    job_id: str
    name: str
    base_config: str
    overrides: dict = field(default_factory=dict)
    status: str = STATUS_QUEUED
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    pid: int | None = None
    exit_code: int | None = None
    error: str = ""
    resume_count: int = 0


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_dirs()
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._load_existing()
        self._scheduler = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._shutdown = threading.Event()
        self._scheduler.start()

    # ── 持久化 ───────────────────────────────────────────────────────────

    def job_dir(self, job_id: str) -> Path:
        return self.settings.jobs_dir / job_id

    def log_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "train.log"

    def out_root(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "out"

    def _save(self, job: Job) -> None:
        path = self.job_dir(job.job_id) / "job.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(job), ensure_ascii=False, indent=2))
        tmp.replace(path)

    def _load_existing(self) -> None:
        for meta in sorted(self.settings.jobs_dir.glob("*/job.json")):
            try:
                job = Job(**json.loads(meta.read_text()))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            # 服务重启恢复：记录为 running 但进程句柄已丢失
            if job.status in (STATUS_RUNNING, STATUS_STOPPING):
                if job.pid and _pid_alive(job.pid):
                    # 训练进程还活着（孤儿进程），继续按 running 跟踪
                    job.status = STATUS_RUNNING
                else:
                    job.status = STATUS_STOPPED
                    job.error = "service restarted while job was running; resume to continue"
                    job.finished_at = job.finished_at or time.time()
                self._save(job)
            self._jobs[job.job_id] = job

    # ── 查询 ────────────────────────────────────────────────────────────

    def list_jobs(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def running_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values()
                       if j.status in (STATUS_RUNNING, STATUS_STOPPING))

    # ── 创建 / 启动 ──────────────────────────────────────────────────────

    def create(self, name: str, base_config: str, overrides: dict) -> Job:
        job = Job(
            job_id=time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
            name=name,
            base_config=base_config,
            overrides=dict(overrides),
            created_at=time.time(),
        )
        with self._lock:
            self.job_dir(job.job_id).mkdir(parents=True, exist_ok=True)
            self._jobs[job.job_id] = job
            self._save(job)
        self._reconcile()
        return job

    def _build_cmd(self, job: Job) -> list[str]:
        python_bin = self.settings.python_bin or sys.executable
        cmd = [python_bin, "scripts/train.py", "--config", job.base_config]
        options = compat._overrides_to_options(job.overrides)
        # out_root 由服务端强制指定为任务目录，用户 overrides 不可覆盖
        options = [o for o in options if not o.startswith("env.out_root=")]
        options.append(f"env.out_root={self.out_root(job.job_id)}")
        cmd.append("--cfg-options")
        cmd.extend(options)
        return cmd

    def _start_locked(self, job: Job) -> None:
        """调用方需持有 self._lock。"""
        cmd = self._build_cmd(job)
        env = compat.build_training_env(
            self.settings.repo_root, [self.settings.secrets_file]
        )
        log_file = open(self.log_path(job.job_id), "ab")
        header = ("" if job.resume_count == 0 else "\n") + "$ " + " ".join(cmd) + "\n"
        log_file.write(header.encode())
        log_file.flush()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(self.settings.repo_root),
                env=env,
                start_new_session=True,  # 独立进程组，便于整组 kill
            )
        except Exception as exc:
            log_file.close()
            job.status = STATUS_FAILED
            job.error = f"failed to spawn training process: {exc}"
            job.finished_at = time.time()
            self._save(job)
            return
        finally:
            # Popen 持有 fd 后父进程侧即可关闭
            if not log_file.closed:
                log_file.close()

        job.status = STATUS_RUNNING
        job.pid = proc.pid
        job.started_at = job.started_at or time.time()
        job.error = ""
        job.exit_code = None
        self._procs[job.job_id] = proc
        self._save(job)
        threading.Thread(target=self._wait_and_finalize,
                         args=(job.job_id, proc), daemon=True).start()

    def _wait_and_finalize(self, job_id: str, proc: subprocess.Popen) -> None:
        exit_code = proc.wait()
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            self._procs.pop(job_id, None)
            job.exit_code = exit_code
            job.finished_at = time.time()
            if job.status == STATUS_STOPPING:
                job.status = STATUS_STOPPED
            elif exit_code == 0:
                job.status = STATUS_SUCCEEDED
            else:
                job.status = STATUS_FAILED
                job.error = f"training process exited with code {exit_code}"
            job.pid = None
            self._save(job)
        self._reconcile()

    # ── 调度 ────────────────────────────────────────────────────────────

    def _reconcile(self) -> None:
        """有空闲槽位时按 FIFO 启动排队任务。"""
        with self._lock:
            slots = self.settings.max_concurrent_jobs - self.running_count()
            if slots <= 0:
                return
            queued = sorted(
                (j for j in self._jobs.values() if j.status == STATUS_QUEUED),
                key=lambda j: j.created_at,
            )
            for job in queued[:slots]:
                self._start_locked(job)

    def _scheduler_loop(self) -> None:
        """兜底轮询：清理孤儿进程状态 + 推进队列。"""
        while not self._shutdown.wait(2.0):
            with self._lock:
                for job in self._jobs.values():
                    # 服务重启后 adopt 的孤儿进程：无 Popen 句柄，靠 pid 探活
                    if (job.status in (STATUS_RUNNING, STATUS_STOPPING)
                            and job.job_id not in self._procs):
                        if job.pid and _pid_alive(job.pid):
                            continue
                        job.finished_at = time.time()
                        job.pid = None
                        if job.status == STATUS_STOPPING:
                            job.status = STATUS_STOPPED
                        else:
                            # 进程结束但退出码不可知（非子进程），按产物判断
                            job.status = (
                                STATUS_SUCCEEDED
                                if (self.out_root(job.job_id) / "best_skill.md").is_file()
                                else STATUS_FAILED
                            )
                            job.error = "orphaned process finished; exit code unknown"
                        self._save(job)
            self._reconcile()

    # ── 停止 / 续训 ─────────────────────────────────────────────────────

    def stop(self, job: Job) -> None:
        with self._lock:
            if job.status == STATUS_QUEUED:
                job.status = STATUS_STOPPED
                job.finished_at = time.time()
                self._save(job)
                return
            if job.status not in (STATUS_RUNNING, STATUS_STOPPING):
                return
            job.status = STATUS_STOPPING
            self._save(job)
            if job.pid:
                try:
                    os.killpg(os.getpgid(job.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError, PermissionError):
                    proc = self._procs.get(job.job_id)
                    if proc is not None:
                        proc.terminate()

    def resume(self, job: Job) -> None:
        """相同 out_root 重新拉起，SkillOpt 自动从 runtime_state.json 续训。"""
        with self._lock:
            job.status = STATUS_QUEUED
            job.resume_count += 1
            job.finished_at = None
            job.error = ""
            self._save(job)
        self._reconcile()

    def close(self) -> None:
        self._shutdown.set()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
