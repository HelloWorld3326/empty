"""训练任务：创建 / 列表 / 详情 / 停止 / 续训 / 日志（增量 + SSE 实时流）。"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .. import progress as prog
from .. import skillopt_compat as compat
from ..auth import AuthDep
from ..errors import bad_request, conflict, not_found, ok
from ..job_manager import FINISHED, STATUS_QUEUED, STATUS_RUNNING, JobManager

router = APIRouter(prefix="/jobs", tags=["jobs"])


class CreateJobRequest(BaseModel):
    name: str = Field(default="", max_length=120, description="任务名，留空自动生成")
    base_config: str = Field(description="benchmark 预设路径，如 configs/docvqa/default.yaml")
    overrides: dict = Field(default_factory=dict,
                            description='结构化覆盖，如 {"train.num_epochs": 4}')
    skip_preflight: bool = Field(default=False, description="跳过模型后端连通性预检")


def _manager(request: Request) -> JobManager:
    return request.app.state.jobs


def _job_view(mgr: JobManager, job) -> dict:
    view = asdict(job)
    view["overrides"] = compat.redact_config(dict(job.overrides))
    view["progress"] = prog.parse_progress(mgr.log_path(job.job_id))
    return view


@router.post("")
async def create_job(body: CreateJobRequest, request: Request, _auth: dict = AuthDep):
    mgr = _manager(request)
    settings = request.app.state.settings
    if body.base_config not in compat.discover_configs(settings.repo_root):
        raise not_found(f"unknown config: {body.base_config}")
    if not body.skip_preflight:
        env = compat.build_training_env(settings.repo_root, [settings.secrets_file])
        error = compat.validate_training_config(
            settings.repo_root, body.base_config, body.overrides, env)
        if error:
            raise bad_request(f"preflight failed: {error}")
    name = body.name or body.base_config.split("/")[-2]
    job = mgr.create(name=name, base_config=body.base_config, overrides=body.overrides)
    return ok({"job_id": job.job_id, "status": job.status})


@router.get("")
async def list_jobs(request: Request, status: str = "", _auth: dict = AuthDep):
    mgr = _manager(request)
    jobs = mgr.list_jobs()
    if status:
        jobs = [j for j in jobs if j.status == status]
    return ok({"jobs": [_job_view(mgr, j) for j in jobs]})


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request, _auth: dict = AuthDep):
    mgr = _manager(request)
    job = mgr.get(job_id)
    if job is None:
        raise not_found(f"job not found: {job_id}")
    return ok(_job_view(mgr, job))


@router.post("/{job_id}/stop")
async def stop_job(job_id: str, request: Request, _auth: dict = AuthDep):
    mgr = _manager(request)
    job = mgr.get(job_id)
    if job is None:
        raise not_found(f"job not found: {job_id}")
    if job.status in FINISHED:
        raise conflict(f"job already finished: {job.status}")
    mgr.stop(job)
    return ok({"job_id": job.job_id, "status": job.status})


@router.post("/{job_id}/resume")
async def resume_job(job_id: str, request: Request, _auth: dict = AuthDep):
    """断点续训：相同 out_root 重新拉起，SkillOpt 从 runtime_state.json 自动恢复。"""
    mgr = _manager(request)
    job = mgr.get(job_id)
    if job is None:
        raise not_found(f"job not found: {job_id}")
    if job.status not in FINISHED:
        raise conflict(f"job is not finished (status={job.status}); stop it first")
    mgr.resume(job)
    return ok({"job_id": job.job_id, "status": job.status})


@router.get("/{job_id}/logs")
async def get_logs(job_id: str, request: Request, offset: int = 0, _auth: dict = AuthDep):
    """增量拉取日志：客户端携带上次返回的 next_offset 轮询。"""
    mgr = _manager(request)
    job = mgr.get(job_id)
    if job is None:
        raise not_found(f"job not found: {job_id}")
    chunk = prog.read_log_chunk(mgr.log_path(job_id), offset)
    chunk["status"] = job.status
    return ok(chunk)


@router.get("/{job_id}/logs/stream")
async def stream_logs(job_id: str, request: Request, offset: int = 0, _auth: dict = AuthDep):
    """SSE 实时流。事件类型：
      log      {"content": "..."}            新日志片段
      progress {"stage_index": ..., ...}     进度快照（约每 2 秒）
      status   {"status": "succeeded", ...}  任务结束（终止事件，随后连接关闭）
    """
    mgr = _manager(request)
    job = mgr.get(job_id)
    if job is None:
        raise not_found(f"job not found: {job_id}")

    async def event_gen():
        pos = offset
        last_progress = None
        while True:
            if await request.is_disconnected():
                return
            chunk = prog.read_log_chunk(mgr.log_path(job_id), pos)
            if chunk["content"]:
                pos = chunk["next_offset"]
                yield {"event": "log", "data": json.dumps(
                    {"content": chunk["content"], "next_offset": pos},
                    ensure_ascii=False)}
            current = prog.parse_progress(mgr.log_path(job_id))
            if current != last_progress:
                last_progress = current
                yield {"event": "progress", "data": json.dumps(current)}
            fresh = mgr.get(job_id)
            if fresh is None or fresh.status in FINISHED:
                # 任务结束：把剩余日志清空后发终止事件
                chunk = prog.read_log_chunk(mgr.log_path(job_id), pos)
                if chunk["content"]:
                    pos = chunk["next_offset"]
                    yield {"event": "log", "data": json.dumps(
                        {"content": chunk["content"], "next_offset": pos},
                        ensure_ascii=False)}
                yield {"event": "status", "data": json.dumps({
                    "status": fresh.status if fresh else "deleted",
                    "exit_code": fresh.exit_code if fresh else None,
                    "error": fresh.error if fresh else "",
                })}
                return
            await asyncio.sleep(1.0)

    return EventSourceResponse(event_gen())
