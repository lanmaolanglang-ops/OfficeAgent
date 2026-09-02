import ipaddress
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from ...rate_limiter import get_rate_limiter


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Protect API endpoints with a process-local sliding-window limit."""

    @staticmethod
    def _policies(request: Request) -> tuple[str, ...]:
        policies = ["api"]
        path = request.url.path
        if request.method in {"POST", "PUT"} and path.startswith("/api/file/upload"):
            policies.append("upload")
        if request.method == "POST" and path == "/api/chat":
            policies.append("model")
        return tuple(policies)

    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/", "/health", "/api/health", "/live", "/ready", "/docs", "/redoc", "/openapi.json"}:
            return await call_next(request)
        identity = self._identity(request)
        checked = []
        limiter = get_rate_limiter()
        for policy in self._policies(request):
            result = limiter.check(policy, identity)
            checked.append((policy, result))
            if not result.allowed:
                response = JSONResponse(status_code=429, content={
                    "success": False, "error_code": "RATE_LIMITED",
                    "message": "请求过于频繁，请稍后重试",
                    "policy": policy,
                })
                response.headers["Retry-After"] = str(max(1, int(result.retry_after)))
                response.headers["X-RateLimit-Policy"] = policy
                return response
        response = await call_next(request)
        policy, result = checked[-1]
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        response.headers["X-RateLimit-Reset"] = str(int(result.reset_at))
        response.headers["X-RateLimit-Policy"] = policy
        return response

    @staticmethod
    def _identity(request: Request) -> str:
        authenticated_user = getattr(request.state, "user_id", "")
        if authenticated_user not in ("", "anonymous"):
            return authenticated_user
        peer = request.client.host if request.client else ""
        try:
            peer_ip = ipaddress.ip_address(peer)
        except ValueError:
            peer_ip = None
        # 仅信任回环反向代理写入的 X-Forwarded-For，避免公网客户端伪造。
        forwarded = request.headers.get("x-forwarded-for", "")
        if peer_ip and peer_ip.is_loopback and forwarded:
            candidate = forwarded.split(",", 1)[0].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
        return peer or "anonymous"
