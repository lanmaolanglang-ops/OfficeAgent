"""
请求日志中间件（已废弃）

新的日志中间件请使用 office_agent.logging_system.RequestLoggingMiddleware
本文件保留仅为向后兼容，不再添加额外 handler。
"""
import time
import os
import json
import logging
from datetime import datetime
from typing import Dict, Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from ..core.config import settings

# 使用统一日志系统，不再在此模块配置 handler
logger = logging.getLogger("office_agent.api")


class RequestLogMiddleware(BaseHTTPMiddleware):
    """
    请求日志中间件（旧版，已被 logging_system.RequestLoggingMiddleware 替代）

    保留用于向后兼容，建议使用新的中间件。
    """

    SKIP_LOG_PATHS = {"/api/health", "/docs", "/openapi.json", "/redoc", "/metrics"}

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        request_id = getattr(request.state, "request_id", str(time.time()))

        body = None
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                raw_body = await request.body()
                body = raw_body.decode("utf-8", errors="ignore")[:2000]
                async def receive():
                    return {"type": "http.request", "body": raw_body}
                request = Request(request.scope, receive)
            except Exception:
                pass

        try:
            response = await call_next(request)
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            logger.error(f"请求失败: {request.method} {request.url.path} - {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error_code": "INTERNAL_ERROR",
                    "message": str(e),
                    "timestamp": datetime.now().isoformat(),
                },
            )

        duration = int((time.time() - start) * 1000)

        if request.url.path not in self.SKIP_LOG_PATHS:
            if response.status_code >= 500:
                logger.error(f"{request.method} {request.url.path} {response.status_code} {duration}ms")
            elif response.status_code >= 400:
                logger.warning(f"{request.method} {request.url.path} {response.status_code} {duration}ms")
            else:
                logger.info(f"{request.method} {request.url.path} {response.status_code} {duration}ms")

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{duration}ms"

        return response
