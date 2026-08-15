import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from ...rate_limiter import get_rate_limiter


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Protect API endpoints with a process-local sliding-window limit."""
    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/", "/health", "/api/health", "/live", "/ready", "/docs", "/openapi.json"}:
            return await call_next(request)
        identity = request.headers.get("X-API-Key") or request.client.host or "anonymous"
        result = get_rate_limiter().check("api", identity)
        if not result.allowed:
            response = JSONResponse(status_code=429, content={
                "success": False, "error_code": "RATE_LIMITED",
                "message": "请求过于频繁，请稍后重试",
            })
            response.headers["Retry-After"] = str(max(1, int(result.retry_after)))
            return response
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        response.headers["X-RateLimit-Reset"] = str(int(result.reset_at))
        return response
