"""
认证中间件（预留，未来对接用户系统）
"""
import time
import uuid
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..core.exceptions import AuthError
from ..core.config import settings


class AuthMiddleware(BaseHTTPMiddleware):
    """认证中间件"""

    # 不需要认证的路径
    PUBLIC_PATHS = {
        "/docs", "/redoc", "/openapi.json",
        "/api/health", "/",
    }

    async def dispatch(self, request: Request, call_next):
        # 认证未开启时直接放行
        if not settings.auth_enabled:
            request.state.user_id = "anonymous"
            request.state.request_id = str(uuid.uuid4())
            return await call_next(request)

        path = request.url.path

        # 公开路径放行
        if path in self.PUBLIC_PATHS or path.startswith("/docs"):
            request.state.user_id = "anonymous"
            request.state.request_id = str(uuid.uuid4())
            return await call_next(request)

        # 检查 API Key
        # Accept API keys only in a header; URLs are persisted by browsers,
        # proxies, analytics systems and request-history logs.
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "error_code": "AUTH_ERROR",
                    "message": "缺少认证信息，请提供 X-API-Key",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
            )

        if api_key not in settings.api_keys:
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "error_code": "AUTH_ERROR",
                    "message": "无效的API Key",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
            )

        request.state.user_id = f"user_{api_key[:8]}"
        request.state.request_id = str(uuid.uuid4())

        return await call_next(request)
