"""监控端点统一 DB 会话依赖注入的针对性测试。

根因背景：main.py 的 5 个监控端点（logs/executions、logs/models、logs/errors、
logs/stats、trace/{id}）此前各自 `SessionLocal()` + try/finally，与会话管理
逻辑分散重复，且无法在测试中替换。现统一为 `Depends(get_db_session)`：
由 FastAPI 保证请求结束后关闭会话，并支持 dependency_overrides 注入假会话。
"""
import types

from starlette.testclient import TestClient


def _fake_session(closed_flags: list):
    return types.SimpleNamespace(close=lambda: closed_flags.append(True))


class TestGetDbSessionDependency:
    def test_yields_session_and_closes_after_use(self, monkeypatch):
        """get_db_session 生成器：产出 SessionLocal 实例并在结束时关闭。"""
        import office_agent.database.session as session_module
        from office_agent.api import deps

        closed = []
        fake = _fake_session(closed)
        monkeypatch.setattr(session_module, "SessionLocal", lambda: fake)

        gen = deps.get_db_session()
        assert next(gen) is fake
        gen.close()
        assert closed == [True]

    def test_closes_session_on_exception(self, monkeypatch):
        """消费方抛异常时会话仍被关闭。"""
        import office_agent.database.session as session_module
        from office_agent.api import deps

        closed = []
        monkeypatch.setattr(session_module, "SessionLocal",
                            lambda: _fake_session(closed))
        gen = deps.get_db_session()
        next(gen)
        try:
            gen.throw(RuntimeError("boom"))
        except RuntimeError:
            pass
        assert closed == [True]


class TestMonitorEndpointsUseDI:
    """通过 dependency_overrides 验证监控端点确实走统一依赖，
    且请求结束后假会话被关闭（证明不再自行 SessionLocal()）。"""

    def _client(self, monkeypatch, closed):
        from office_agent.api.deps import get_db_session
        from office_agent.api.main import create_app
        import office_agent.database.repository as repo_module

        class _FakeRepo:
            def __init__(self, session):
                self.session = session

            def find(self, limit=100, order_by=None, descending=False):
                return []

            def get_by_task(self, task_id):
                return []

            def get_by_trace(self, trace_id):
                return []

            def get_recent(self, limit=100, resolved=None):
                return []

            def get_stats(self, hours):
                return {"total": 0}

        for name in ("ExecutionLogRepository", "ModelCallLogRepository",
                     "ErrorLogRepository"):
            monkeypatch.setattr(repo_module, name, _FakeRepo)

        app = create_app()

        def _override():
            fake = _fake_session(closed)
            try:
                yield fake
            finally:
                fake.close()

        app.dependency_overrides[get_db_session] = _override

        # 不进入上下文管理器：跳过 lifespan（不启动 worker/scheduler/DB）
        return TestClient(app, base_url="http://localhost")

    def test_execution_logs_endpoint_uses_overridden_session(self, monkeypatch):
        closed = []
        client = self._client(monkeypatch, closed)
        response = client.get("/api/logs/executions")
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert closed == [True]

    def test_log_stats_endpoint_uses_overridden_session(self, monkeypatch):
        closed = []
        client = self._client(monkeypatch, closed)
        response = client.get("/api/logs/stats")
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert closed == [True]

    def test_trace_endpoint_uses_overridden_session(self, monkeypatch):
        closed = []
        client = self._client(monkeypatch, closed)
        response = client.get("/api/trace/abc-123")
        assert response.status_code == 200
        assert response.json()["data"]["trace_id"] == "abc-123"
        assert closed == [True]

    def test_monitor_endpoints_do_not_call_session_local_directly(self):
        """源码级守卫：监控端点区域内不再出现 SessionLocal() 直连。"""
        import inspect
        from office_agent.api import main as main_module

        src = inspect.getsource(main_module)
        for marker in ('"/api/logs/executions"', '"/api/logs/models"',
                       '"/api/logs/errors"', '"/api/logs/stats"',
                       '"/api/trace/{trace_id}"'):
            start = src.index(marker)
            # 端点函数体截止于下一个路由或 on_startup（startup 恢复逻辑
            # 合法直连 SessionLocal，不属于监控端点范畴）
            candidates = [pos for pos in (
                src.find("@app.get", start + 1),
                src.find("async def on_startup", start + 1),
            ) if pos != -1]
            end = min(candidates) if candidates else len(src)
            body = src[start:end]
            assert "SessionLocal" not in body
            assert "get_db_session" in body
