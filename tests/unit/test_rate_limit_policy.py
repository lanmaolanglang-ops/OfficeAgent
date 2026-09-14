"""P5-3：rate limit policy 一致性回归。

本轮确认并钉住的真实策略：
- 限流身份：已认证用户优先（兼容钩子），回退 ``resolve_request_client``
  解析的客户端地址（trusted-proxy 感知）；中间件运行时序在 Auth 之前，
  实际按客户端地址限流（本地桌面产品语义）；
- 三个桶 api(5/min)/model(2/min)/upload(2/3600s) 按端点分层独立计数，
  chat 同时消耗 api+model 两桶；
- 429 走统一错误 envelope（RATE_LIMITED）+ Retry-After +
  X-RateLimit-Policy；未注册策略显式 fail-open（可用性优先的契约）；
- 计数器为进程内存态、重启即重置（本地桌面产品定位）。

本轮清理：``get_rate_limiter`` 注册了 ``model_calls`` 令牌桶但仓库内
从未有任何 ``consume("model_calls")`` 调用——误导性死配置，已移除。
"""
import pytest
from starlette.testclient import TestClient

import office_agent.api.middleware.rate_limit as rl_module
import office_agent.rate_limiter as limiter_module
from office_agent.api.middleware.rate_limit import RateLimitMiddleware
from office_agent.rate_limiter import RateLimiterManager


@pytest.fixture
def limiter():
    manager = RateLimiterManager()
    manager.add_limit("api", 5, 60)
    manager.add_limit("model", 2, 60)
    manager.add_limit("upload", 2, 3600)
    return manager


@pytest.fixture
def client(limiter, monkeypatch):
    """带限流的测试应用：客户端身份固定为单回环地址。"""
    monkeypatch.setattr(rl_module, "get_rate_limiter", lambda: limiter)
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[
        Route("/api/task", ok, methods=["POST"]),
        Route("/api/chat", ok, methods=["POST"]),
        Route("/api/file/upload", ok, methods=["POST"]),
    ])
    app.add_middleware(RateLimitMiddleware)
    return TestClient(app)


class TestPolicyBehavior:
    def test_burst_boundary_n_and_n_plus_one(self, client):
        """api 桶 limit=5：第 N 个放行、第 N+1 个 429（滑动窗口边界）。"""
        for _ in range(5):
            assert client.post("/api/task").status_code == 200
        sixth = client.post("/api/task")
        assert sixth.status_code == 429
        assert sixth.json()["error_code"] == "RATE_LIMITED"

    def test_retry_after_header_present(self, client):
        for _ in range(5):
            client.post("/api/task")
        sixth = client.post("/api/task")
        assert int(sixth.headers["Retry-After"]) >= 1
        assert sixth.headers["X-RateLimit-Policy"] == "api"

    def test_model_bucket_is_separate_from_api(self, client):
        """api 桶与 model 桶独立计数：POST /api/chat 同时消耗两桶。"""
        assert client.post("/api/chat").status_code == 200   # api=1, model=1
        assert client.post("/api/chat").status_code == 200   # api=2, model=2 满
        # model 桶耗尽：api 仍有配额，第 3 次 chat 由 model 桶拒绝
        third = client.post("/api/chat")
        assert third.status_code == 429
        assert third.headers["X-RateLimit-Policy"] == "model"
        # model 桶被拒不影响 api 桶的独立配额
        assert client.post("/api/task").status_code == 200

    def test_upload_bucket_only_counts_upload_initiation(self, client):
        """upload 桶独立于 api 桶，且只对 upload/initiation 端点计数。"""
        assert client.post("/api/file/upload").status_code == 200
        assert client.post("/api/file/upload").status_code == 200
        third = client.post("/api/file/upload")
        assert third.status_code == 429
        assert third.headers["X-RateLimit-Policy"] == "upload"
        # api 桶不受 upload 消费影响（5 次内仍可用）
        assert client.post("/api/task").status_code == 200

    def test_health_skip_paths_unlimited(self, client):
        # SKIP_PATHS 端点不消耗也不受限——通过直接构造验证
        rl = RateLimitMiddleware(_dummy_app())
        assert "/api/health" in rl._SKIP_PATHS
        assert "/api/task" not in rl._SKIP_PATHS


def _dummy_app():
    async def app(scope, receive, send):
        pass
    return app


class TestLimiterUnit:
    def test_window_reset_with_injected_clock(self):
        """窗口滑动：用注入时钟验证过期请求移出后重新放行（无真实等待）。

        SlidingWindowLimiter 使用 time.monotonic（P2-8），注入 clock 参数。
        """
        clock_now = [1000.0]
        limiter = limiter_module.SlidingWindowLimiter(
            max_requests=2, window_seconds=60, clock=lambda: clock_now[0],
        )
        assert limiter.is_allowed("ip1").allowed
        assert limiter.is_allowed("ip1").allowed
        assert limiter.is_allowed("ip1").allowed is False  # 满了
        clock_now[0] += 61  # 窗口滑过
        assert limiter.is_allowed("ip1").allowed, "窗口过期后必须重新放行"

    def test_identity_isolation_between_clients(self):
        limiter = limiter_module.SlidingWindowLimiter(max_requests=1, window_seconds=60)
        assert limiter.is_allowed("10.0.0.1").allowed
        assert limiter.is_allowed("10.0.0.2").allowed, "不同客户端各自计数"
        assert limiter.is_allowed("10.0.0.1").allowed is False

    def test_unknown_policy_fails_open_documented(self):
        """未注册策略显式放行（可用性优先的既有契约，钉住防漂移）。"""
        manager = RateLimiterManager()
        result = manager.check("typo-policy", "ip")
        assert result.allowed is True

    def test_dead_model_calls_bucket_removed(self):
        manager = RateLimiterManager()
        manager.add_limit("api", 3, 60)
        import office_agent.rate_limiter as rl
        # get_rate_limiter 初始化后不应再有无人消费的 model_calls 桶
        real = rl.get_rate_limiter()
        try:
            assert "model_calls" not in real._buckets
        finally:
            rl._rate_limiter = None
