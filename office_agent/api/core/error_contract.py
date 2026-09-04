"""
统一 API 错误响应契约（唯一权威来源）。

所有错误响应共享同一 envelope：

    {
        "success": False,
        "error_code": "<稳定错误码>",
        "message": "<用户可读信息>",
        "details": <可选细节, 仅 debug 或参数校验时非 None>,
        "timestamp": "<UTC ISO8601>"
    }

错误码分四类：

- 业务错误：由 ``APIError`` 层级自带（exceptions.py），handler 透传；
- 参数校验：``VALIDATION_ERROR``（RequestValidationError / 422）；
- HTTP 状态：``ERROR_CODE_BY_STATUS`` 把裸 ``HTTPException``
  的状态码映射为稳定代码，路由无需逐个改写；
- 基础设施/第三方：``DOMAIN_ERROR_MAPPINGS`` 把存储（OSError 系）、
  数据库（SQLAlchemyError）、第三方上游（URLError/ConnectionError/
  TimeoutError，模型网关客户端走 urllib）映射为带安全通用信息的
  契约响应，避免向客户端泄漏 SQL、路径等内部细节。
"""
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import SQLAlchemyError
from urllib.error import URLError


def build_error_envelope(
    error_code: str,
    message: str,
    details: Any = None,
) -> dict:
    """构造统一错误响应体。"""
    return {
        "success": False,
        "error_code": error_code,
        "message": message,
        "details": details,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# HTTP 状态码 -> 稳定错误码（裸 HTTPException 的契约化映射）
ERROR_CODE_BY_STATUS = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_ERROR",
    503: "SERVICE_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}


def error_code_for_status(status_code: int) -> str:
    """状态码映射为稳定错误码；未收录的状态保留 HTTP_<code> 兜底。"""
    return ERROR_CODE_BY_STATUS.get(status_code, f"HTTP_{status_code}")


# 领域异常 -> (status_code, error_code, 安全通用信息)
# 匹配按异常 MRO 精度进行（见 domain_error_response_args）：
# FileNotFoundError/TimeoutError/URLError 均为 OSError 子类，
# 会优先命中自身映射而非 OSError。
DOMAIN_ERROR_MAPPINGS = [
    # 数据库故障：不泄漏 SQL/连接串
    (SQLAlchemyError, 500, "DATABASE_ERROR", "数据库服务暂时不可用"),
    # 存储错误：文件缺失与底层 I/O 分开计价
    (FileNotFoundError, 404, "FILE_NOT_FOUND", "请求的文件不存在"),
    (OSError, 500, "STORAGE_ERROR", "存储服务暂时不可用"),
    # 第三方/上游（模型网关走 urllib；超时与连接失败区分）
    (TimeoutError, 504, "UPSTREAM_TIMEOUT", "上游服务响应超时"),
    (URLError, 502, "UPSTREAM_ERROR", "上游服务连接失败"),
    (ConnectionError, 502, "UPSTREAM_ERROR", "上游服务连接失败"),
]

_DOMAIN_MAP = {exc_type: (status, code, msg) for exc_type, status, code, msg in DOMAIN_ERROR_MAPPINGS}


def domain_error_response_args(exc: Exception) -> Optional[tuple]:
    """若异常命中领域映射，返回 (status_code, error_code, message)。

    按 ``type(exc).__mro__`` 顺序查找，保证子类映射优先于基类
    （与 Starlette exception handler 的查找语义一致）。
    """
    for klass in type(exc).__mro__:
        hit = _DOMAIN_MAP.get(klass)
        if hit is not None:
            return hit
    return None
