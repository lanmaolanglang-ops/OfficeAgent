from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse

from ...rate_limiter import get_rate_limiter


class RateLimitMiddleware:
    """Protect API endpoints with a process-local sliding-window limit.

    纯 ASGI 实现（原为 BaseHTTPMiddleware）：不为每个请求额外启动下游
    任务与消息队列，被拒请求零缓冲直接返回；通过包装 send 在
    http.response.start 上注入限流响应头，行为与原实现一致。
    """

    _SKIP_PATHS = frozenset({
        "/", "/health", "/api/health", "/live", "/ready",
        "/docs", "/redoc", "/openapi.json",
    })

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _policies(request: Request) -> tuple[str, ...]:
        policies = ["api"]
        path = request.url.path
        if request.method in {"POST", "PUT"} and path.startswith("/api/file/upload"):
            policies.append("upload")
        if request.method == "POST" and path == "/api/chat":
            policies.append("model")
        return tuple(policies)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if request.url.path in self._SKIP_PATHS:
            await self.app(scope, receive, send)
            return

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
                await response(scope, receive, send)
                return

        policy, result = checked[-1]
        rate_headers = {
            "X-RateLimit-Remaining": str(result.remaining),
            "X-RateLimit-Reset": str(int(result.reset_at)),
            "X-RateLimit-Policy": policy,
        }

        async def send_with_rate_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message.setdefault("headers", []))
                for name, value in rate_headers.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_rate_headers)

    @staticmethod
    def _identity(request: Request) -> str:
        authenticated_user = getattr(request.state, "user_id", "")
        if authenticated_user not in ("", "anonymous"):
            return authenticated_user
        # 统一走权威客户端身份解析：仅 trusted proxy 的转发头会被采信
        from ...security.client_identity import resolve_request_client
        return resolve_request_client(request)
