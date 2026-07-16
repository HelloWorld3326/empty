"""训练产物：分数曲线 / best_skill.md / 技能版本列表 / 文件下载。"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from ..auth import AuthDep
from ..errors import bad_request, not_found, ok
from ..job_manager import JobManager

router = APIRouter(prefix="/jobs", tags=["results"])


def _manager(request: Request) -> JobManager:
    return request.app.state.jobs


def _require_job(mgr: JobManager, job_id: str):
    job = mgr.get(job_id)
    if job is None:
        raise not_found(f"job not found: {job_id}")
    return job


@router.get("/{job_id}/results")
async def get_results(job_id: str, request: Request, _auth: dict = AuthDep):
    """汇总产物（对应 SkillOpt out_root 目录结构）：
      history      history.json 的每步指标（前端画分数折线图）
      best_skill   best_skill.md 内容（deliverable）
      skills       skills/skill_v*.md 版本列表
    """
    mgr = _manager(request)
    _require_job(mgr, job_id)
    out = mgr.out_root(job_id)

    history = None
    history_file = out / "history.json"
    if history_file.is_file():
        try:
            history = json.loads(history_file.read_text())
        except (json.JSONDecodeError, OSError):
            history = None

    best_skill = None
    best_file = out / "best_skill.md"
    if best_file.is_file():
        best_skill = best_file.read_text(errors="replace")

    skills = []
    skills_dir = out / "skills"
    if skills_dir.is_dir():
        for p in sorted(skills_dir.glob("*.md")):
            skills.append({
                "name": p.name,
                "path": f"skills/{p.name}",
                "size": p.stat().st_size,
            })

    return ok({
        "history": history,
        "best_skill": best_skill,
        "skills": skills,
        "has_runtime_state": (out / "runtime_state.json").is_file(),
    })


@router.get("/{job_id}/artifacts/{path:path}")
async def download_artifact(job_id: str, path: str, request: Request, _auth: dict = AuthDep):
    """下载 out_root 内的任意产物文件；resolve 后强校验在 out_root 内，防路径穿越。"""
    mgr = _manager(request)
    _require_job(mgr, job_id)
    out = mgr.out_root(job_id).resolve()
    target = (out / path).resolve()
    if not str(target).startswith(str(out) + os.sep):
        raise bad_request("artifact path escapes job output directory")
    if not target.is_file():
        raise not_found(f"artifact not found: {path}")
    return FileResponse(target, filename=target.name)
