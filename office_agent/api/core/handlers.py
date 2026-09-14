"""
统一异常处理器

所有错误响应的 envelope 与错误码口径统一由
``api/core/error_contract.py`` 定义；本模块只负责把各类异常
接入 FastAPI 的 exception handler 机制：

- ``APIError`` 层级（业务错误）：透传其 error_code/status_code；
- ``RequestValidationError``：VALIDATION_ERROR，逐字段展开；
- 裸 ``HTTPException``：状态码经 ``error_code_for_status`` 映射为
  稳定错误码，路由侧无需逐个改写；
- 领域异常（存储 OSError 系 / 数据库 SQLAlchemyError / 第三方
  URLError、TimeoutError、ConnectionError）：映射为带安全通用
  信息的契约响应，内部细节仅 debug 模式经 sanitizer 输出；
- 其余未捕获异常：INTERNAL_ERROR 500。
"""
import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .error_contract import (
    DOMAIN_ERROR_MAPPINGS,
    build_error_envelope,
    error_code_for_status,
)
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
            content=build_error_envelope(exc.error_code, exc.message, exc.details),
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
            content=build_error_envelope("VALIDATION_ERROR", "请求参数校验失败", errors),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        # dict detail 必须保留结构（code/message/fields），不得 str() 成
        # "{'code': ...}" 字符串再塞进 message（P2-14）。
        detail = exc.detail
        default_code = error_code_for_status(exc.status_code)
        if isinstance(detail, dict):
            message = detail.get("message") or detail.get("detail") or default_code
            code = detail.get("code") or default_code
            content = build_error_envelope(code, str(message), detail)
        elif isinstance(detail, str):
            content = build_error_envelope(default_code, detail)
        else:
            content = build_error_envelope(default_code, str(detail))
        return JSONResponse(status_code=exc.status_code, content=content)

    def _make_domain_handler(error_code: str, status_code: int, message: str):
        async def domain_error_handler(request: Request, exc: Exception):
            logger.warning(
                f"{error_code}: {type(exc).__name__}: {exc}",
                extra={"path": request.url.path, "method": request.method},
            )
            return JSONResponse(
                status_code=status_code,
                content=build_error_envelope(
                    error_code,
                    message,
                    sanitize_error(exc) if _is_debug() else None,
                ),
            )

        return domain_error_handler

    # 存储 / 数据库 / 第三方上游异常 -> 统一契约
    for exc_type, status_code, error_code, message in DOMAIN_ERROR_MAPPINGS:
        app.exception_handler(exc_type)(
            _make_domain_handler(error_code, status_code, message)
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        logger.exception(f"Unhandled error: {exc}")
        return JSONResponse(
            status_code=500,
            content=build_error_envelope(
                "INTERNAL_ERROR",
                "服务器内部错误",
                sanitize_error(exc) if _is_debug() else None,
            ),
        )


def _is_debug() -> bool:
    from .config import settings
    return settings.debug
