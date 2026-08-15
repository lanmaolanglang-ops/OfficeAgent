"""
FastAPI 中间件

- Request ID 注入
- 请求日志
- 指标收集
- 全链路追踪
"""
import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from .context import (
    set_request_id, set_trace_id, set_user_id,
    get_request_id, get_trace_id, get_context_dict,
    generate_request_id, generate_trace_id,
)
from .logger import get_logger
from .metrics import registry
from .tracer import get_trace_context

logger = get_logger("api")

# 不记录日志的路径
SKIP_LOG_PATHS = {"/metrics", "/health", "/favicon.ico"}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    请求日志中间件

    1. 为每个请求生成/透传 request_id 和 trace_id
    2. 记录请求开始/结束
    3. 收集指标
    4. 错误捕获
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 跳过不需要日志的路径
        path = request.url.path
        if path in SKIP_LOG_PATHS:
            return await call_next(request)

        # 生成 request_id（优先使用客户端传入的）
        request_id = request.headers.get("X-Request-ID") or generate_request_id()
        trace_id = request.headers.get("X-Trace-ID") or generate_trace_id()

        # 设置上下文
        req_token = set_request_id(request_id)
        trace_token = set_trace_id(trace_id)

        # 尝试从 header 获取 user_id
        user_id = request.headers.get("X-User-ID", "")
        user_token = None
        if user_id:
            from .context import set_user_id
            user_token = set_user_id(user_id)

        # 指标
        registry.gauge("system_active_requests").inc()
        start = time.time()

        # 记录请求
        logger.info(
            f"→ {request.method} {path}",
            extra={
                "method": request.method,
                "path": path,
                "query": str(request.url.query)[:200] if request.url.query else "",
                "client": request.client.host if request.client else "",
                "user_agent": request.headers.get("user-agent", "")[:200],
            },
        )

        response = None
        error = None
        try:
            response = await call_next(request)
            return response
        except Exception as e:
            error = e
            raise
        finally:
            duration = time.time() - start
            duration_ms = duration * 1000
            status_code = response.status_code if response else 500

            # 指标
            registry.counter("api_requests_total").inc(
                method=request.method, path=path, status=str(status_code)
            )
            registry.histogram("api_request_duration_seconds").observe(
                duration, method=request.method, path=path
            )
            if status_code >= 400:
                registry.counter("api_errors_total").inc(
                    method=request.method, path=path,
                    error_type=str(status_code),
                )
            registry.gauge("system_active_requests").dec()

            # 响应头注入 request_id
            if response:
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Trace-ID"] = trace_id

            # 日志
            log_level = logger.info
            if status_code >= 500:
                log_level = logger.error
            elif status_code >= 400:
                log_level = logger.warning

            log_level(
                f"← {request.method} {path} {status_code} {duration_ms:.0f}ms",
                extra={
                    "method": request.method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )

            # 重置上下文
            from .context import _request_id_var, _trace_id_var, _user_id_var
            _request_id_var.reset(req_token)
            _trace_id_var.reset(trace_token)
            if user_token:
                _user_id_var.reset(user_token)


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """全局错误捕获中间件"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            return await call_next(request)
        except Exception as e:
            from fastapi.responses import JSONResponse
            import traceback

            logger.error(
                f"未捕获异常: {type(e).__name__}: {e}",
                exc_info=True,
                extra={
                    "path": request.url.path,
                    "method": request.method,
                },
            )

            registry.counter("system_errors_total").inc(
                error_type=type(e).__name__
            )

            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": {
                        "type": type(e).__name__,
                        "message": str(e),
                        "request_id": get_request_id(),
                    },
                },
            )
