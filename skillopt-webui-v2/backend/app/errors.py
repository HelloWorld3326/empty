"""统一错误码与响应格式。

所有接口返回 ``{"code": int, "message": str, "data": ...}``：
  0      成功
  40001  参数错误 / 配置校验失败（含训练预检失败，message 为可读的中文/英文原因）
  40101  未认证或 token 无效
  40401  资源不存在（任务 / 配置 / 产物）
  40901  状态冲突（如对运行中的任务重复启动、对未结束的任务续训）
  50001  服务内部错误
"""
from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(self, code: int, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def bad_request(message: str) -> ApiError:
    return ApiError(40001, message, 400)


def unauthorized(message: str = "unauthorized") -> ApiError:
    return ApiError(40101, message, 401)


def not_found(message: str) -> ApiError:
    return ApiError(40401, message, 404)


def conflict(message: str) -> ApiError:
    return ApiError(40901, message, 409)


def ok(data: Any = None) -> dict:
    return {"code": 0, "message": "ok", "data": data}
