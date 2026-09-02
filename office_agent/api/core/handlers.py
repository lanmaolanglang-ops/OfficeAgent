"""
统一异常处理器
"""
import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .exceptions import APIError
from ...security.error_sanitizer import sanitize_error

logger = logging.getLogger("office_agent.api")


def register_exception_handlers(app):
    """注册所有异常处理器"""

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        logger.warning(f"API Error: {exc.error_code} - {exc.message}")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error_code": exc.error_code,
                "message": exc.message,
                "details": exc.details,
                "timestamp": _now(),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        errors = []
        for err in exc.errors():
            errors.append({
                "field": ".".join(str(location) for location in err.get("loc", [])),
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            })
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error_code": "VALIDATION_ERROR",
                "message": "请求参数校验失败",
                "details": errors,
                "timestamp": _now(),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error_code": f"HTTP_{exc.status_code}",
                "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
                "timestamp": _now(),
            },
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        logger.exception(f"Unhandled error: {exc}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error_code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
                "details": sanitize_error(exc) if _is_debug() else None,
                "timestamp": _now(),
            },
        )


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _is_debug() -> bool:
    from .config import settings
    return settings.debug
