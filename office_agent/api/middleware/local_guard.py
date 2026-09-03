"""
本地访问防护中间件

桌面应用后端绑定在 127.0.0.1，但用户浏览器里任何网页都可以向
http://127.0.0.1:8765 发起跨站请求（form / no-cors fetch，CORS 拦不住
"简单请求"），甚至通过 DNS rebinding 读取响应。本中间件做两层防护：

1. Host 校验：只接受 localhost / 127.0.0.1（含 IPv6 [::1]），阻断 DNS
   rebinding（rebinding 请求的 Host 是攻击者域名）。
2. Origin 校验：浏览器附带的 Origin 必须在本应用白名单内；非浏览器
   客户端（curl、Tauri 后端探针、同源 GET）不携带 Origin，直接放行。
"""
import logging
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from ..core.config import settings

logger = logging.getLogger("office_agent.api.local_guard")

ALLOWED_ORIGINS = frozenset(settings.cors_origins)

ALLOWED_HOSTS = {
    "localhost", "127.0.0.1", "::1",
}


def _hostname(host_header: str) -> str:
    """Parse hostname without breaking bracketed IPv6 literals."""
    try:
        return (urlsplit(f"//{host_header}").hostname or "").lower()
    except ValueError:
        return ""


class LocalGuardMiddleware:
    """拦截跨站浏览器请求与 DNS rebinding。

    纯 ASGI 实现（原为 BaseHTTPMiddleware）：校验只依赖请求头，
    命中拒绝时零缓冲直接返回，不为每个请求启动下游任务。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        host_header = request.headers.get("host") or ""
        host = _hostname(host_header)
        if not host_header or not host:
            response = JSONResponse(status_code=403, content={
                "success": False,
                "error_code": "FORBIDDEN_HOST",
                "message": "拒绝访问：非法 Host",
            })
            await response(scope, receive, send)
            return
        if host and host not in ALLOWED_HOSTS:
            response = JSONResponse(status_code=403, content={
                "success": False,
                "error_code": "FORBIDDEN_HOST",
                "message": "拒绝访问：非法 Host",
            })
            await response(scope, receive, send)
            return

        origin = request.headers.get("origin")
        if origin:
            if origin not in ALLOWED_ORIGINS:
                logger.warning("Blocked cross-site origin: %s %s", origin, request.url.path)
                response = JSONResponse(status_code=403, content={
                    "success": False,
                    "error_code": "FORBIDDEN_ORIGIN",
                    "message": "拒绝访问：非法来源",
                })
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
