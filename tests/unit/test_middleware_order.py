"""中间件执行顺序与纯 ASGI 转换行为对等的针对性测试。

根因背景：
1. main.py 注释称"日志中间件最先添加最后执行包裹所有请求"，但 Starlette
   语义是后添加者更靠外先执行，且限流位于认证内层，无法在 JWT 校验
   开销之前挡量。现执行顺序为 SizeLimit → LocalGuard → RateLimit →
   Auth → Logging → Error → CORS。
2. RateLimit/LocalGuard 原为 BaseHTTPMiddleware，每个请求额外启动
   下游任务与消息队列；已改为纯 ASGI，拒绝路径零缓冲直接返回。
"""
import time

from fastapi import FastAPI
from starlette.testclient import TestClient

from office_agent.rate_limiter import RateLimitResult


def _deny_all(_policy, _identity):
    return RateLimitResult(allowed=False, remaining=0,
                           reset_at=time.time() + 30, retry_after=30.0)


def _allow_all(_policy, _identity):
    return RateLimitResult(allowed=True, remaining=7,
                           reset_at=time.time() + 60)


def _build_app(monkeypatch, rate_first: bool):
    """按 main.py 的注册顺序（或相反顺序）构建最小 app。"""
    from office_agent.api.middleware.auth import AuthMiddleware
    from office_agent.api.middleware.local_guard import LocalGuardMiddleware
    from office_agent.api.middleware.rate_limit import RateLimitMiddleware
    from office_agent.api.core.config import settings

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_keys", ["secret-key"])

    app = FastAPI()
    # Starlette：后添加者更靠外层、先执行
    if rate_first:
        app.add_middleware(AuthMiddleware)
        app.add_middleware(RateLimitMiddleware)
    else:
        app.add_middleware(RateLimitMiddleware)
        app.add_middleware(AuthMiddleware)
    app.add_middleware(LocalGuardMiddleware)

    @app.get("/api/who")
    def who():
        return {"ok": True}

    return TestClient(app, base_url="http://localhost")


class TestMiddlewareOrder:
    def test_rate_limit_sheds_before_auth_when_outer(self, monkeypatch):
        """限流在认证之外：超限请求未带凭据也应得到 429 而非 401。"""
        from office_agent.api.middleware import rate_limit as rl_module
        monkeypatch.setattr(rl_module, "get_rate_limiter",
                            lambda: type("L", (), {"check": staticmethod(_deny_all)})())
        client = _build_app(monkeypatch, rate_first=True)
        response = client.get("/api/who")  # 无凭据
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "30"
        assert response.headers["X-RateLimit-Policy"] == "api"

    def test_auth_rejects_first_when_rate_limit_inner(self, monkeypatch):
        """对照组：旧顺序（限流在内）下同一请求先被认证拒为 401。"""
        from office_agent.api.middleware import rate_limit as rl_module
        monkeypatch.setattr(rl_module, "get_rate_limiter",
                            lambda: type("L", (), {"check": staticmethod(_deny_all)})())
        client = _build_app(monkeypatch, rate_first=False)
        response = client.get("/api/who")
        assert response.status_code == 401

    def test_main_py_registers_rate_limit_outside_auth(self):
        """源码级守卫：main.py 中 AuthMiddleware 必须先于 RateLimit 注册
        （先注册 = 更内层），即限流在认证之外执行。"""
        import inspect
        from office_agent.api import main as main_module
        src = inspect.getsource(main_module)
        auth_pos = src.index("app.add_middleware(AuthMiddleware)")
        rate_pos = src.index("app.add_middleware(RateLimitMiddleware)")
        assert auth_pos < rate_pos


class TestPureAsgiParity:
    def test_success_response_carries_rate_headers(self, monkeypatch):
        from office_agent.api.middleware import rate_limit as rl_module
        monkeypatch.setattr(rl_module, "get_rate_limiter",
                            lambda: type("L", (), {"check": staticmethod(_allow_all)})())
        client = _build_app(monkeypatch, rate_first=True)
        response = client.get("/api/who", headers={"X-API-Key": "secret-key"})
        assert response.status_code == 200
        assert response.headers["X-RateLimit-Remaining"] == "7"
        assert "X-RateLimit-Reset" in response.headers
        assert response.headers["X-RateLimit-Policy"] == "api"

    def test_skip_paths_bypass_limiter(self, monkeypatch):
        from office_agent.api.middleware import rate_limit as rl_module
        calls = []
        monkeypatch.setattr(
            rl_module, "get_rate_limiter",
            lambda: type("L", (), {"check": staticmethod(
                lambda p, i: calls.append(p) or _deny_all(p, i))})())
        client = _build_app(monkeypatch, rate_first=True)
        response = client.get("/api/health")
        assert response.status_code == 404  # 最小 app 无此路由，但不应被限流
        assert calls == []

    def test_local_guard_still_rejects_bad_host(self, monkeypatch):
        from office_agent.api.middleware import rate_limit as rl_module
        monkeypatch.setattr(rl_module, "get_rate_limiter",
                            lambda: type("L", (), {"check": staticmethod(_allow_all)})())
        client = _build_app(monkeypatch, rate_first=True)
        response = client.get("/api/who", headers={"host": "evil.example.com"})
        assert response.status_code == 403
        assert response.json()["error_code"] == "FORBIDDEN_HOST"

    def test_local_guard_rejects_disallowed_origin(self, monkeypatch):
        from office_agent.api.middleware import rate_limit as rl_module
        monkeypatch.setattr(rl_module, "get_rate_limiter",
                            lambda: type("L", (), {"check": staticmethod(_allow_all)})())
        client = _build_app(monkeypatch, rate_first=True)
        response = client.get("/api/who",
                              headers={"Origin": "http://evil.example.com"})
        assert response.status_code == 403
        assert response.json()["error_code"] == "FORBIDDEN_ORIGIN"
