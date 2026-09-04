"""StreamingResponse 跨中间件流式行为回归。

根因背景（清单「通用 Middleware」节）：
- ``BaseHTTPMiddleware`` 历史上曾整体缓冲响应体；starlette 1.3.1 已改为
  rendezvous 内存通道逐 chunk 透传。本文件用可观测 generator 证明：
  当前真实中间件栈（与 main.py 同序）不会把 StreamingResponse 整体
  预读入内存，且 headers / background / 普通 JSON 契约不回归。
- 背压证明绕过 TestClient/httpx 传输层（它会聚合小 chunk），直接以
  ASGI 协议驱动 app：``send`` 由最外层中间件逐个 await，首个
  http.response.body 消息到达时记录 generator 已产出 chunk 数。
- 若未来 starlette 升级或中间件改写重新引入缓冲，这些测试会立即变红。
"""
import asyncio
import threading

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.testclient import TestClient

from office_agent.rate_limiter import RateLimitResult


def _allow_all(_policy, _identity):
    return RateLimitResult(allowed=True, remaining=99,
                           reset_at=1e12, retry_after=0.0)


def _build_app(monkeypatch, tmp_path):
    """按 main.py 注册顺序构建真实中间件栈的最小 app。"""
    from office_agent.api.core.config import settings
    from office_agent.api.middleware import rate_limit as rl_module
    from office_agent.api.middleware.auth import AuthMiddleware
    from office_agent.api.middleware.local_guard import LocalGuardMiddleware
    from office_agent.api.middleware.rate_limit import RateLimitMiddleware
    from office_agent.api.middleware.request_size import RequestSizeLimitMiddleware
    from office_agent.logging_system import (
        ErrorHandlingMiddleware, RequestLoggingMiddleware,
    )

    # 日志系统首次使用时惰性初始化：把数据根重定向到临时目录，
    # 避免测试向真实用户目录写日志/数据库
    monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(
        rl_module, "get_rate_limiter",
        lambda: type("L", (), {"check": staticmethod(_allow_all)})(),
    )

    app = FastAPI()
    # 与 main.py 相同注册顺序（后注册者更靠外）：
    # Logging → Auth → RateLimit → LocalGuard → ErrorHandling → SizeLimit
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(LocalGuardMiddleware)
    app.add_middleware(ErrorHandlingMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware)
    return app


def _make_counted_app(monkeypatch, tmp_path, chunk_count, chunk_size):
    """挂载一个可观测 generator 的 /counted 流式端点。"""
    app = _build_app(monkeypatch, tmp_path)
    state = {"produced": 0}

    async def counted_stream():
        for index in range(chunk_count):
            state["produced"] += 1
            yield bytes([index % 251]) * chunk_size

    @app.get("/counted")
    def counted_endpoint():
        return StreamingResponse(counted_stream(),
                                 media_type="application/octet-stream")

    return app, state


async def _drive_asgi(app, path, state, produced_at_first):
    """直接以 ASGI 协议驱动 app，返回 (status, headers, body)。

    ``send`` 被最外层中间件逐个 await；首个 http.response.body 消息到达
    时把 generator 已产出 chunk 数记入 ``produced_at_first[0]``——
    无缓冲透传时它只能轻微领先，整体缓冲时它会等于总 chunk 数。
    """
    scope = {
        "type": "http", "http_version": "1.1", "asgi": {"version": "3.0"},
        "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [(b"host", b"localhost")],
        "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8765),
    }
    result = {"body_parts": []}
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        # 请求体已消费；此后唯一合法消息是 disconnect。
        # 测试客户端不断开——挂起即可，starlette 会在响应结束时取消它。
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.start":
            result["status"] = message["status"]
            result["headers"] = {
                k.decode().lower(): v.decode()
                for k, v in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            if produced_at_first[0] is None:
                produced_at_first[0] = state["produced"]
            result["body_parts"].append(message.get("body", b""))
        await asyncio.sleep(0)

    await app(scope, receive, send)
    return result["status"], result["headers"], b"".join(result["body_parts"])


@pytest.fixture
def stack_client(monkeypatch, tmp_path):
    """TestClient 版（headers / background / JSON / 异常契约用）。"""
    app = _build_app(monkeypatch, tmp_path)

    state = {"background_ran": threading.Event()}

    async def observable_stream():
        for index in range(100):
            yield f"chunk-{index:04d}|".encode()

    def on_background():
        state["background_ran"].set()

    from starlette.background import BackgroundTask

    @app.get("/stream")
    def stream_endpoint():
        return StreamingResponse(
            observable_stream(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="report.bin"',
                "X-Custom-Marker": "stream-marker",
            },
            background=BackgroundTask(on_background),
        )

    @app.get("/json")
    def json_endpoint():
        return JSONResponse({"success": True, "value": 42})

    @app.get("/boom")
    def boom_endpoint():
        raise RuntimeError("route exploded before streaming")

    client = TestClient(app, base_url="http://localhost")
    client.test_state = state
    return client


class TestStreamingBackpressure:
    TOTAL = 100

    def test_first_chunk_arrives_before_generator_completes(self, monkeypatch, tmp_path):
        """首个 chunk 到达时 generator 必须远未完成（证明逐 chunk 流式）。"""
        app, state = _make_counted_app(monkeypatch, tmp_path, self.TOTAL, 16)
        produced_at_first = [None]
        status, _, body = asyncio.run(
            _drive_asgi(app, "/counted", state, produced_at_first))
        assert status == 200
        assert produced_at_first[0] is not None
        # 全缓冲实现此刻 produced == TOTAL；流式实现只领先少量 chunk
        assert produced_at_first[0] < self.TOTAL // 2
        assert len(body) == self.TOTAL * 16
        assert state["produced"] == self.TOTAL

    def test_large_body_is_not_preloaded(self, monkeypatch, tmp_path):
        """4MB 流式 body：首个 64KB 到达时 generator 不得接近耗尽。"""
        app, state = _make_counted_app(monkeypatch, tmp_path, 64, 65536)
        produced_at_first = [None]
        status, _, body = asyncio.run(
            _drive_asgi(app, "/counted", state, produced_at_first))
        assert status == 200
        assert produced_at_first[0] is not None
        assert produced_at_first[0] < 64
        assert len(body) == 64 * 65536
        assert state["produced"] == 64


class TestStreamingPreservedAcrossMiddleware:
    def test_streaming_headers_and_background_preserved(self, stack_client):
        state = stack_client.test_state
        with stack_client.stream("GET", "/stream") as response:
            assert response.headers["Content-Disposition"] == (
                'attachment; filename="report.bin"'
            )
            assert response.headers["Content-Type"] == "application/octet-stream"
            assert response.headers["X-Custom-Marker"] == "stream-marker"
            # 日志中间件注入的响应头仍应存在
            assert "X-Request-ID" in response.headers
            for _ in response.iter_raw():
                pass
        assert state["background_ran"].is_set()

    def test_normal_json_response_unaffected(self, stack_client):
        response = stack_client.get("/json")
        assert response.status_code == 200
        assert response.json() == {"success": True, "value": 42}

    def test_exception_before_stream_start_keeps_json_contract(self, stack_client):
        """流开始前路由抛异常：必须返回 JSON 错误契约而非裸文本 500。"""
        response = stack_client.get("/boom")
        assert response.status_code == 500
        payload = response.json()
        assert payload["success"] is False
        assert payload["error_code"] == "INTERNAL_ERROR"


class TestErrorHandlingBoundary:
    """ErrorHandlingMiddleware 与 exception handlers 的职责边界。

    - 路由/业务异常：由最内层 exception handlers 按契约处理；
    - 中间件层（Auth/Logging/RateLimit/LocalGuard）抛出的异常：
      handlers 够不到，必须由外移后的 ErrorHandlingMiddleware 捕获，
      否则落到 Starlette ServerErrorMiddleware 返回裸文本 500。
    两处 500 形状统一为 error_code=INTERNAL_ERROR。
    """

    def test_middleware_layer_exception_gets_json_contract(
            self, monkeypatch, tmp_path):
        """中间件层（Auth 位置）抛异常 → JSON 契约 500，而非裸文本。"""
        from office_agent.api.core.config import settings
        from office_agent.api.middleware.local_guard import LocalGuardMiddleware
        from office_agent.api.middleware.request_size import (
            RequestSizeLimitMiddleware,
        )
        from office_agent.logging_system import ErrorHandlingMiddleware

        monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(settings, "auth_enabled", False)

        class RaisingMiddleware:
            """模拟 Auth 位置的中间件抛异常。"""

            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if scope["type"] == "http":
                    raise RuntimeError("middleware exploded")
                await self.app(scope, receive, send)

        app = FastAPI()
        # 按 main.py 注册顺序：Raising 占据 Auth 的位置
        app.add_middleware(RaisingMiddleware)
        app.add_middleware(LocalGuardMiddleware)
        app.add_middleware(ErrorHandlingMiddleware)
        app.add_middleware(RequestSizeLimitMiddleware)

        @app.get("/api/who")
        def who():
            return {"ok": True}

        client = TestClient(app, base_url="http://localhost")
        response = client.get("/api/who")
        assert response.status_code == 500
        payload = response.json()
        assert payload["success"] is False
        assert payload["error_code"] == "INTERNAL_ERROR"
        assert payload["message"] == "服务器内部错误"

    def test_main_py_registers_error_handling_outside_inner_middleware(self):
        """源码守卫：main.py 中 ErrorHandling 必须在 Auth/RateLimit/
        LocalGuard 之后注册（更靠外层），才能覆盖中间件层异常。"""
        import inspect
        from office_agent.api import main as main_module
        src = inspect.getsource(main_module)
        error_pos = src.index("app.add_middleware(ErrorHandlingMiddleware)")
        for inner in ("AuthMiddleware", "RateLimitMiddleware",
                      "LocalGuardMiddleware"):
            assert src.index(f"app.add_middleware({inner})") < error_pos
        # RequestSizeLimit 保持最外层（廉价拒绝优先）
        size_pos = src.index("app.add_middleware(RequestSizeLimitMiddleware)")
        assert error_pos < size_pos
