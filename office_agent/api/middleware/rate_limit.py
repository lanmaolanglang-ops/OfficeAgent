from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse

from ...rate_limiter import get_rate_limiter
from ..core.error_contract import build_error_envelope


class RateLimitMiddleware:
    """Protect API endpoints with a process-local sliding-window limit.

    纯 ASGI 实现（原为 BaseHTTPMiddleware）：不为每个请求额外启动下游
    任务与消息队列，被拒请求零缓冲直接返回；通过包装 send 在
    http.response.start 上注入限流响应头，行为与原实现一致。

    限流身份（policy 设计）：优先已认证用户（``request.state.user_id``，
    由内层 AuthMiddleware 设置——注意本中间件在其外层先执行，该分支
    是为调用序变化预留的兼容钩子，运行时通常走回退）；回退到
    ``resolve_request_client`` 解析的客户端地址（仅信任 trusted proxy
    的转发头）。本地桌面部署（单用户/回环）下按客户端地址限流即产品
    语义。计数器为进程内内存态，进程重启即重置（本地产品定位）。
    """

    _SKIP_PATHS = frozenset({
        "/", "/health", "/api/health", "/live", "/ready",
        "/docs", "/redoc", "/openapi.json",
    })

    # upload 桶语义 = “一次文件上传”：普通上传与分片上传的 initiation
    # 各计一次；分片上传的 chunk/complete 不再重复消费，避免一个文件的
    # 每个分片都被视为一次完整上传而放大 N 倍计数。chunk 本身仍受
    # api 桶与请求体大小守卫约束。
    _UPLOAD_COUNTED_PATHS = frozenset({
        "/api/file/upload",
        "/api/file/upload/multipart/init",
    })

    def __init__(self, app):
        self.app = app

    @classmethod
    def _policies(cls, request: Request) -> tuple[str, ...]:
        policies = ["api"]
        path = request.url.path
        if request.method in {"POST", "PUT"} and path in cls._UPLOAD_COUNTED_PATHS:
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
                response = JSONResponse(status_code=429, content=build_error_envelope(
                    "RATE_LIMITED", "请求过于频繁，请稍后重试",
                    {"policy": policy, "retry_after": max(1, int(result.retry_after))},
                ))
                response.headers["Retry-After"] = str(max(1, int(result.retry_after)))
                response.headers["X-RateLimit-Policy"] = policy
                await response(scope, receive, send)
                return

        # 多策略：暴露最严格的 remaining 与最早的 reset（P2-20），
        # 而不是只显示最后一个策略。
        strictest = min(checked, key=lambda item: item[1].remaining)
        earliest = min(checked, key=lambda item: item[1].reset_at)
        rate_headers = {
            "X-RateLimit-Remaining": str(strictest[1].remaining),
            "X-RateLimit-Reset": str(int(earliest[1].reset_at)),
            "X-RateLimit-Policy": ",".join(p for p, _ in checked),
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
        # 已认证身份优先（兼容钩子：user_id 由内层 AuthMiddleware 写入；
        # 本中间件先于其执行，运行时通常落到下面的客户端地址回退）。
        authenticated_user = getattr(request.state, "user_id", "")
        if authenticated_user not in ("", "anonymous"):
            return authenticated_user
        # 统一走权威客户端身份解析：仅 trusted proxy 的转发头会被采信。
        from ...security.client_identity import resolve_request_client
        return resolve_request_client(request)
