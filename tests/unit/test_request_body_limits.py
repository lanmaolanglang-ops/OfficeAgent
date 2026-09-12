"""P3-3：请求体大小守卫回归（Content-Length 早拒 + chunked 流式累计）。

修复前：守卫只检查声明的 Content-Length 且只覆盖上传路径——
无 Content-Length 的 chunked 请求完全绕过前置检查；非上传端点没有
任何体积上限；413 响应也不符合统一错误 envelope、无 request id。
"""
import json

import pytest
from starlette.responses import PlainTextResponse

from office_agent.api.middleware.request_size import (
    DEFAULT_API_BODY_LIMIT,
    RequestSizeLimitMiddleware,
)

UPLOAD_PATH = "/api/file/upload"
UPLOAD_LIMIT = 1000
MULTIPART_OVERHEAD = 1024 * 1024


async def _counting_app(scope, receive, send, inner):
    """读完整请求体并回显长度的最小应用（模拟 FastAPI 路由）。"""
    inner["calls"] += 1
    if scope.get("type") != "http":
        return
    total = 0
    disconnected = False
    while True:
        message = await receive()
        if message["type"] == "http.request":
            body = message.get("body", b"")
            inner["bodies"].append(body)
            total += len(body)
            if not message.get("more_body", False):
                break
        elif message["type"] == "http.disconnect":
            disconnected = True
            break
    if not disconnected:
        response = PlainTextResponse(f"received:{total}")
        await response(scope, receive, send)


def _make_app(max_file_size=UPLOAD_LIMIT, **middleware_kwargs):
    inner: dict = {"calls": 0, "bodies": []}

    async def app(scope, receive, send):
        await _counting_app(scope, receive, send, inner)

    wrapped = RequestSizeLimitMiddleware(app, max_file_size=max_file_size,
                                         **middleware_kwargs)
    return wrapped, inner


def _scope(path=UPLOAD_PATH, method="POST", content_length=None,
           content_type=None):
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    if content_type is not None:
        headers.append((b"content-type", content_type.encode()))
    return {"type": "http", "method": method, "path": path,
            "headers": headers, "query_string": b""}


def _collect_send(sent):
    async def send(message):
        sent.append(message)
    return send


async def _noop_receive():
    import asyncio
    await asyncio.Event().wait()


def _body_receive(body: bytes):
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return receive


def _disconnected_receive():
    async def receive():
        return {"type": "http.disconnect"}
    return receive


def _chunked_receive(chunks):
    pending = [{"type": "http.request", "body": chunk,
                "more_body": index < len(chunks) - 1}
               for index, chunk in enumerate(chunks)]

    async def receive():
        if len(pending) > 1:
            return pending.pop(0)
        return pending[0]
    return receive


def _status_of(sent):
    for message in sent:
        if message["type"] == "http.response.start":
            return message["status"]
    return None


def _body_of(sent):
    for message in sent:
        if message["type"] == "http.response.body":
            return json.loads(message["body"])
    return {}


def _header_of(sent, name):
    for message in sent:
        if message["type"] == "http.response.start":
            for key, value in message.get("headers", []):
                if key.decode().lower() == name:
                    return value.decode()
    return None


class TestDeclaredContentLength:
    @pytest.mark.anyio
    async def test_upload_over_limit_rejected_before_app(self):
        app, inner = _make_app()
        scope = _scope(content_length=UPLOAD_LIMIT + 1)
        sent = []
        await app(scope, _noop_receive, _collect_send(sent))
        assert inner["calls"] == 0, "超限请求不得到达下游应用"
        assert _status_of(sent) == 413
        assert _body_of(sent)["error_code"] == "PAYLOAD_TOO_LARGE"
        assert _body_of(sent)["message"] == "文件超过大小上限"
        assert _header_of(sent, "x-request-id")

    @pytest.mark.anyio
    async def test_upload_at_limit_passes(self):
        app, inner = _make_app()
        scope = _scope(content_length=UPLOAD_LIMIT)
        sent = []
        await app(scope, _body_receive(b"x" * UPLOAD_LIMIT), _collect_send(sent))
        assert inner["calls"] == 1
        assert _status_of(sent) == 200

    @pytest.mark.anyio
    async def test_multipart_gets_overhead_allowance(self):
        app, inner = _make_app()
        limit = UPLOAD_LIMIT + MULTIPART_OVERHEAD
        scope = _scope(content_length=limit,
                       content_type="multipart/form-data; boundary=x")
        sent = []
        await app(scope, _body_receive(b"y"), _collect_send(sent))
        assert inner["calls"] == 1
        assert _status_of(sent) == 200

    @pytest.mark.anyio
    async def test_multipart_over_limit_rejected(self):
        app, inner = _make_app()
        limit = UPLOAD_LIMIT + MULTIPART_OVERHEAD
        scope = _scope(content_length=limit + 1,
                       content_type="multipart/form-data; boundary=x")
        sent = []
        await app(scope, _noop_receive, _collect_send(sent))
        assert inner["calls"] == 0
        assert _status_of(sent) == 413

    @pytest.mark.anyio
    async def test_non_upload_api_body_cap(self):
        """非上传端点受全局保守上限约束（更严于上传上限）。"""
        app, inner = _make_app(api_body_limit=500)
        scope = _scope(path="/api/chat", content_length=501)
        sent = []
        await app(scope, _noop_receive, _collect_send(sent))
        assert inner["calls"] == 0
        assert _status_of(sent) == 413
        assert _body_of(sent)["message"] == "请求体超过大小上限"

    @pytest.mark.anyio
    async def test_global_cap_does_not_cripple_uploads(self):
        """上传端点按自己的更大上限执行，不被全局上限硬压。"""
        app, inner = _make_app(api_body_limit=10)
        scope = _scope(content_length=UPLOAD_LIMIT)
        sent = []
        await app(scope, _body_receive(b"z"), _collect_send(sent))
        assert inner["calls"] == 1
        assert _status_of(sent) == 200


class TestChunkedStreamingGuard:
    @pytest.mark.anyio
    async def test_chunked_cannot_bypass(self):
        """无 Content-Length 的分块请求：流式累计超限必须被拦下。"""
        app, inner = _make_app(api_body_limit=600)
        scope = _scope(path="/api/chat")  # 无 content-length
        sent = []
        total = {"n": 0}

        async def receive():
            # 模拟上游持续分块推送（每次 400 字节，总量无界）
            total["n"] += 400
            return {"type": "http.request",
                    "body": b"a" * 400, "more_body": True}

        async def body_reader(scope, receive, send):
            # 下游持续读 body：第二次累计即超限，receive 抛内部信号
            while True:
                message = await receive()
                if not message.get("more_body", False):
                    break

        app.app = body_reader
        await app(scope, receive, _collect_send(sent))
        assert inner["calls"] == 0
        assert _status_of(sent) == 413
        assert _body_of(sent)["error_code"] == "PAYLOAD_TOO_LARGE"
        assert total["n"] > 600

    @pytest.mark.anyio
    async def test_chunked_under_limit_passes_intact(self):
        """未超限的分块请求：字节块原样透传，下游拿到完整请求体。"""
        app, inner = _make_app(api_body_limit=600)
        scope = _scope(path="/api/chat")
        sent = []
        body = b"b" * 500

        await app(scope, _chunked_receive([body[:200], body[200:400],
                                           body[400:]]),
                  _collect_send(sent))
        assert inner["calls"] == 1
        assert b"".join(inner["bodies"]) == body, "分块必须原样透传"
        assert _status_of(sent) == 200

    @pytest.mark.anyio
    async def test_upload_chunked_over_limit_rejected(self):
        """分块上传同样受上传上限约束（非 multipart：上限=文件上限）。

        流式守卫的语义：下游应用会被调用，但首次读体即触发中断，
        因此应用不得发出 200 响应，最终响应必须是 413。
        """
        app, inner = _make_app()
        scope = _scope()  # 无 content-length、非 multipart
        sent = []

        async def receive():
            return {"type": "http.request",
                    "body": b"c" * (UPLOAD_LIMIT + 5), "more_body": False}

        await app(scope, receive, _collect_send(sent))
        assert _status_of(sent) == 413
        assert all(message["type"] != "http.response.start"
                   or message["status"] != 200 for message in sent), \
            "超限请求不得产生 200 响应"

    @pytest.mark.anyio
    async def test_consecutive_requests_no_state_pollution(self):
        """连续请求的累计计数互不污染（每次请求独立闭包）。"""
        app, inner = _make_app(api_body_limit=600)
        for _ in range(3):
            sent = []
            scope = _scope(path="/api/chat")
            await app(scope, _chunked_receive([b"x" * 100, b"y" * 100]),
                      _collect_send(sent))
            assert _status_of(sent) == 200
        assert len(inner["bodies"]) == 6  # 3 次 × 2 块，全部透传


class TestProtocolEdgeCases:
    @pytest.mark.anyio
    async def test_get_without_body_passes(self):
        app, inner = _make_app(api_body_limit=100)
        scope = _scope(path="/api/health", method="GET")
        sent = []
        await app(scope, _disconnected_receive(), _collect_send(sent))
        assert inner["calls"] == 1, "无请求体的 GET 不被拦截"

    @pytest.mark.anyio
    async def test_non_http_scope_passthrough(self):
        app, inner = _make_app()
        scope = {"type": "lifespan"}
        sent = []
        await app(scope, _disconnected_receive(), _collect_send(sent))
        assert inner["calls"] == 1  # 非 http 请求原样透传给下游应用
        assert sent == []           # 守卫不插手 lifespan 语义

    @pytest.mark.anyio
    async def test_malformed_content_length_goes_to_streaming_guard(self):
        """畸形 Content-Length（非数字）按"未知"走流式守卫，不炸不误拒。"""
        app, inner = _make_app(max_file_size=100)
        headers = [(b"content-length", b"garbage")]
        scope = {"type": "http", "method": "POST", "path": UPLOAD_PATH,
                 "headers": headers, "query_string": b""}
        sent = []
        await app(scope, _body_receive(b"small"), _collect_send(sent))
        assert inner["calls"] == 1
        assert _status_of(sent) == 200

    def test_default_api_body_limit(self):
        assert DEFAULT_API_BODY_LIMIT == 10 * 1024 * 1024

    def test_env_override_for_api_limit(self, monkeypatch):
        monkeypatch.setenv("MAX_API_BODY_SIZE", "12345")
        middleware = RequestSizeLimitMiddleware(_dummy_app())
        assert middleware.api_body_limit == 12345

    def test_limits_per_path_type(self):
        middleware = RequestSizeLimitMiddleware(
            _dummy_app(), max_file_size=100, api_body_limit=7)
        upload_limit, upload_msg = middleware._limit_for({}, is_upload=True)
        api_limit, api_msg = middleware._limit_for({}, is_upload=False)
        assert upload_limit == 100 and "文件" in upload_msg
        assert api_limit == 7 and "请求体" in api_msg


def _dummy_app():
    async def app(scope, receive, send):
        pass
    return app
