"""SkillOpt WebUI v2 — FastAPI 服务入口。

    uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8600

前端构建产物放到 backend/static/ 后由本服务托管（/ 路径）；未放置时仅提供
API 与 Swagger（/docs）。经 nexent 网关子路径代理时无需特殊配置：所有接口
都在 /api 前缀下，前端要求使用相对路径与 hash router（见前端需求文档）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .errors import ApiError
from .job_manager import JobManager
from .routers import configs, jobs, results, secrets, system
from .settings import Settings

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(
        title="SkillOpt WebUI v2 API",
        description="Web 按钮化的 SkillOpt 训练控制台：配置表单、任务启停、日志流、产物下载。",
        version="1.0.0",
    )
    app.state.settings = settings
    app.state.jobs = JobManager(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message, "data": None},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={"code": 40001, "message": str(exc.errors()), "data": None},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(_request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"code": 50001, "message": f"internal error: {exc}", "data": None},
        )

    for router in (system.router, configs.router, jobs.router,
                   results.router, secrets.router):
        app.include_router(router, prefix="/api")

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    @app.on_event("shutdown")
    async def shutdown():
        app.state.jobs.close()

    return app
