"""请求体大小前置守卫。

两层语义（全局兜底 + 上传专属上限，不硬压所有端点）：

- 上传端点（``/api/file/upload``）：``max_file_size``（multipart 额外留
  协议开销），与路由内口径一致；
- 其余 HTTP 端点：``api_body_limit`` 全局保守上限（普通 JSON 请求远
  用不到该量级）。

守卫手段：

1. 声明了 Content-Length 且超限：解析请求体**之前**直接返回确定性
   413，全程不读取请求体；
2. 未声明 Content-Length（chunked/流式上传）：以纯 ASGI ``receive``
   包装流式累计已到达的字节数，超限立即中断并向客户端返回 413——
   分块传输无法绕过；未超限时字节块原样透传，下游拿到完整请求体；
3. 实现为纯 ASGI 中间件（非 BaseHTTPMiddleware），不缓冲流式上传/下载。

413 响应使用统一错误 envelope（build_error_envelope）并携带
``X-Request-ID``（该请求尚未到达鉴权中间件、无既有 request id，此处
生成一个作为本次响应的规范 ID）。
"""
import os
import uuid

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ...storage.validators import DEFAULT_MAX_SIZE
from ..core.error_contract import build_error_envelope, error_code_for_status

# 与 api/router/file.py 路由内口径一致：仅 multipart 额外留 1MB 协议开销
_MULTIPART_OVERHEAD = 1024 * 1024

_GUARDED_UPLOAD_PREFIXES = ("/api/file/upload",)

# 普通 API（JSON 等）请求体的全局保守上限
DEFAULT_API_BODY_LIMIT = 10 * 1024 * 1024


class _BodyLimitExceeded(Exception):
    """流式累计超过请求体上限（内部信号，不向客户端泄露）。"""


class RequestSizeLimitMiddleware:
    """在解析请求体之前按声明/累计大小返回 413。"""

    def __init__(self, app: ASGIApp, max_file_size: int | None = None,
                 multipart_overhead: int = _MULTIPART_OVERHEAD,
                 api_body_limit: int | None = None):
        self.app = app
        if max_file_size is None:
            max_file_size = int(os.environ.get("MAX_FILE_SIZE", DEFAULT_MAX_SIZE))
        self.max_file_size = max_file_size
        self.multipart_overhead = multipart_overhead
        if api_body_limit is None:
            api_body_limit = int(os.environ.get(
                "MAX_API_BODY_SIZE", DEFAULT_API_BODY_LIMIT))
        self.api_body_limit = max(1, int(api_body_limit))

    def _limit_for(self, headers: dict, is_upload: bool) -> tuple[int, str]:
        """返回 (该请求的体积上限, 超限提示)。"""
        if is_upload:
            content_type = headers.get(b"content-type", b"").decode("latin-1").lower()
            limit = self.max_file_size
            if content_type.startswith("multipart/form-data"):
                limit += self.multipart_overhead
            return limit, "文件超过大小上限"
        return self.api_body_limit, "请求体超过大小上限"

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        path = scope.get("path", "")
        is_upload = path.startswith(_GUARDED_UPLOAD_PREFIXES)
        limit, message = self._limit_for(headers, is_upload)

        declared = headers.get(b"content-length", b"").decode("latin-1").strip()
        if declared.isdigit():
            # Content-Length 已知：解析请求体之前尽早拒绝
            if int(declared) > limit:
                await self._send_rejection(scope, receive, send, message)
                return
            await self.app(scope, receive, send)
            return

        # 无 Content-Length（chunked/流式）：流式累计守卫，分块无法绕过
        await self._guard_streaming(scope, receive, send, limit, message)

    async def _guard_streaming(self, scope: Scope, receive: Receive,
                               send: Send, limit: int, message: str):
        total = 0
        response_started = False

        async def counting_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    raise _BodyLimitExceeded()
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyLimitExceeded:
            # 下游尚未开始响应时才能发送 413；否则交由服务器断开连接
            if not response_started:
                await self._send_rejection(scope, receive, send, message)

    async def _send_rejection(self, scope: Scope, receive: Receive,
                              send: Send, message: str):
        response = JSONResponse(
            status_code=413,
            content=build_error_envelope(
                error_code_for_status(413), message, None),
            headers={"X-Request-ID": str(uuid.uuid4())},
        )
        await response(scope, receive, send)
