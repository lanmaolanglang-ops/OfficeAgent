"""上传请求体大小前置守卫。

根因：路由内的 413 检查只有在 FastAPI 完成 ``File(...)`` 依赖校验后才会
执行；非 multipart 的超限请求会先被 422 拦下，拒绝行为随 Content-Type
不同而不稳定。本守卫在路由校验之前按声明的 Content-Length 统一返回
确定性 413，且全程不读取请求体。

实现为纯 ASGI 中间件（非 BaseHTTPMiddleware），不会缓冲流式上传/下载。
"""
import os

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ...storage.validators import DEFAULT_MAX_SIZE

# 与 api/router/file.py 路由内口径一致：仅 multipart 额外留 1MB 协议开销
_MULTIPART_OVERHEAD = 1024 * 1024

_GUARDED_PREFIXES = ("/api/file/upload",)


class RequestSizeLimitMiddleware:
    """对上传端点在解析请求体之前按声明大小返回 413。"""

    def __init__(self, app: ASGIApp, max_file_size: int | None = None,
                 multipart_overhead: int = _MULTIPART_OVERHEAD):
        self.app = app
        if max_file_size is None:
            max_file_size = int(os.environ.get("MAX_FILE_SIZE", DEFAULT_MAX_SIZE))
        self.max_file_size = max_file_size
        self.multipart_overhead = multipart_overhead

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope.get("path", "").startswith(
            _GUARDED_PREFIXES
        ):
            headers = dict(scope.get("headers") or [])
            declared = headers.get(b"content-length", b"").decode("latin-1").strip()
            if declared.isdigit():
                content_type = headers.get(b"content-type", b"").decode("latin-1").lower()
                limit = self.max_file_size
                if content_type.startswith("multipart/form-data"):
                    limit += self.multipart_overhead
                if int(declared) > limit:
                    response = JSONResponse(
                        status_code=413,
                        content={"detail": "文件超过大小上限"},
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
