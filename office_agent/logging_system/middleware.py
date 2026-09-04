"""
FastAPI 中间件

- Request ID 注入
- 请求日志
- 指标收集
- 全链路追踪
"""
import time
import re
from datetime import datetime, timezone
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .context import (
    set_request_id, set_trace_id, generate_request_id, generate_trace_id,
)
from .logger import get_logger
from .metrics import registry

logger = get_logger("api")

_CONTEXT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _safe_context_id(value: str | None, generator: Callable[[], str]) -> str:
    return value if value and _CONTEXT_ID_RE.fullmatch(value) else generator()

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

        # 内部关联 ID 始终由服务端生成。客户端 ID 只作为已校验的附加上下文，
        # 不能控制日志主键或跨请求串联关系。
        request_id = generate_request_id()
        trace_id = generate_trace_id()
        request.state.client_request_id = _safe_context_id(
            request.headers.get("X-Request-ID"), lambda: ""
        )
        request.state.client_trace_id = _safe_context_id(
            request.headers.get("X-Trace-ID"), lambda: ""
        )

        # 设置上下文
        req_token = set_request_id(request_id)
        trace_token = set_trace_id(trace_id)

        # 用户身份只能来自认证中间件，不能信任客户端自报的 X-User-ID。
        user_id = getattr(request.state, "user_id", "")
        user_token = None
        if user_id:
            from .context import set_user_id
            user_token = set_user_id(user_id)

        # 指标
        registry.gauge("system_active_requests").inc()
        start = time.time()

        # 记录请求（client 统一走权威身份解析，反代后仍记录真实来源）
        from ..security.client_identity import resolve_request_client
        logger.info(
            f"→ {request.method} {path}",
            extra={
                "method": request.method,
                "path": path,
                "query": str(request.url.query)[:200] if request.url.query else "",
                "client": resolve_request_client(request),
                "user_agent": request.headers.get("user-agent", "")[:200],
            },
        )

        response = None
        try:
            response = await call_next(request)
            return response
        except Exception:
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
                    # ErrorHandlingMiddleware 已记录带堆栈的异常；状态行不要重复落库。
                    "skip_db_log": status_code >= 500,
                },
            )

            # 重置上下文
            from .context import _request_id_var, _trace_id_var, _user_id_var
            _request_id_var.reset(req_token)
            _trace_id_var.reset(trace_token)
            if user_token:
                _user_id_var.reset(user_token)


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """全局错误捕获中间件。

    职责边界（与 ``api/core/handlers.py`` 的 exception handlers 互补，不重叠）：

    - exception handlers 注册在最内层，负责**路由与业务异常**的契约化响应
      （APIError / 参数校验 / HTTPException / 路由内未捕获 Exception）；
    - 本中间件注册在除 RequestSizeLimit 之外的最外层（见 main.py），负责
      **中间件层（Auth / Logging / RateLimit / LocalGuard / CORS）抛出、
      exception handlers 够不到**的异常——否则这些异常会落到 Starlette
      ServerErrorMiddleware，返回裸文本 500，破坏 API JSON 契约。

    两处产出的 500 响应形状保持同一契约（error_code=INTERNAL_ERROR）。
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            return await call_next(request)
        except Exception as e:
            from fastapi.responses import JSONResponse
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

            from ..security.error_sanitizer import sanitize_error
            from ..api.core.config import settings
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error_code": "INTERNAL_ERROR",
                    "message": "服务器内部错误",
                    "details": sanitize_error(e) if settings.debug else None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
