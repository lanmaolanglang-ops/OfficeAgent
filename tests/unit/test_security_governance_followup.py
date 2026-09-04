"""Security Governance 收尾专项测试（清单：upload 限流消费 + 细粒度审计）。

钉住的规则：
- upload 桶由限流中间件真实消费，语义为“一次文件上传”：普通上传与
  分片 initiation 各计一次，chunk/complete 不重复消费；
- 限流身份复用权威 client_identity 解析（trusted proxy 才采信 XFF）；
- 超限响应使用统一 Error Contract（429 + RATE_LIMITED envelope）；
- 任务终态（success/failed/cancelled）与每次逻辑模型调用进入
  AuditLog，仅含安全元数据，审计存储失败对业务 fail-open。
"""
import asyncio
import json
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from office_agent.rate_limiter import SlidingWindowLimiter, get_rate_limiter


# ---------------------------------------------------------------
# 工具：直接驱动 RateLimitMiddleware 的 ASGI 调用
# ---------------------------------------------------------------

def _asgi_call(middleware_app, path, method="POST", peer="10.0.0.1",
               headers=None):
    scope = {
        "type": "http", "method": method, "path": path,
        "query_string": b"", "scheme": "http",
        "server": ("testserver", 80), "client": (peer, 12345),
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in (headers or {}).items()
        ],
    }
    result = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            result["status"] = message["status"]
        elif message["type"] == "http.response.body":
            result["body"] = message.get("body", b"")

    asyncio.run(middleware_app(scope, receive, send))
    return result


def _downstream(status=200):
    async def app(scope, receive, send):
        body = json.dumps({"ok": status == 200}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})
    return app


@pytest.fixture()
def rate_limit_env(monkeypatch):
    """隔离 upload/api 桶配额，测试后恢复原 limiter。"""
    from office_agent.api.middleware.rate_limit import RateLimitMiddleware

    limiter = get_rate_limiter()
    saved = dict(limiter._limits)
    limiter._limits["upload"] = SlidingWindowLimiter(2, 3600)
    limiter._limits["api"] = SlidingWindowLimiter(100, 60)
    try:
        yield RateLimitMiddleware
    finally:
        limiter._limits.clear()
        limiter._limits.update(saved)


def _policies(path, method="POST"):
    from types import SimpleNamespace

    from office_agent.api.middleware.rate_limit import RateLimitMiddleware

    request = SimpleNamespace(url=SimpleNamespace(path=path), method=method)
    return set(RateLimitMiddleware._policies(request))


# ---------------------------------------------------------------
# Upload 桶消费节点
# ---------------------------------------------------------------

class TestUploadRateLimitPolicy:
    def test_normal_upload_consumes_upload_bucket(self):
        policies = _policies("/api/file/upload")
        assert "upload" in policies and "api" in policies

    def test_multipart_init_consumes_once(self):
        assert "upload" in _policies("/api/file/upload/multipart/init")

    def test_chunks_and_complete_do_not_consume(self):
        assert "upload" not in _policies(
            "/api/file/upload/multipart/file_1/part")
        assert "upload" not in _policies(
            "/api/file/upload/multipart/file_1/complete")
        assert "upload" not in _policies(
            "/api/file/upload/multipart/file_1/abort")

    def test_non_post_and_non_upload_paths_do_not_consume(self):
        assert "upload" not in _policies("/api/file/upload", method="GET")
        assert "upload" not in _policies("/api/file/download/file_1")


class TestUploadRateLimitEnforcement:
    def test_normal_upload_over_quota_uses_error_contract(self,
                                                          rate_limit_env):
        app = rate_limit_env(_downstream())
        assert _asgi_call(app, "/api/file/upload")["status"] == 200
        assert _asgi_call(app, "/api/file/upload")["status"] == 200
        rejected = _asgi_call(app, "/api/file/upload")
        assert rejected["status"] == 429
        payload = json.loads(rejected["body"])
        # 统一 Error Contract envelope
        assert payload["success"] is False
        assert payload["error_code"] == "RATE_LIMITED"
        assert isinstance(payload["message"], str) and payload["message"]
        assert isinstance(payload["timestamp"], str)
        assert payload["details"]["policy"] == "upload"

    def test_multipart_chunks_do_not_multiply_consumption(self,
                                                          rate_limit_env):
        app = rate_limit_env(_downstream())
        base = "/api/file/upload/multipart"
        assert _asgi_call(app, f"{base}/init")["status"] == 200
        # 同一文件的多个 chunk + complete 不再消费 upload 桶
        for _ in range(5):
            assert _asgi_call(app, f"{base}/file_1/part")["status"] == 200
        assert _asgi_call(app, f"{base}/file_1/complete")["status"] == 200
        # 第二次 initiation 仍有余额；第三次被限
        assert _asgi_call(app, f"{base}/init")["status"] == 200
        assert _asgi_call(app, f"{base}/init")["status"] == 429

    def test_failed_upload_still_consumes_admission_slot(self,
                                                         rate_limit_env):
        """计数语义：入场即计、失败不退还（保守、防重试冲刷）。"""
        app = rate_limit_env(_downstream(status=500))
        assert _asgi_call(app, "/api/file/upload")["status"] == 500
        assert _asgi_call(app, "/api/file/upload")["status"] == 500
        assert _asgi_call(app, "/api/file/upload")["status"] == 429

    def test_api_bucket_independent_of_upload_exhaustion(self,
                                                         rate_limit_env):
        app = rate_limit_env(_downstream())
        for _ in range(2):
            assert _asgi_call(app, "/api/file/upload")["status"] == 200
        assert _asgi_call(app, "/api/file/upload")["status"] == 429
        # upload 桶耗尽不影响普通 API（api 桶独立）
        assert _asgi_call(app, "/api/task/", method="GET")["status"] == 200

    def test_different_clients_isolated_behind_trusted_proxy(
            self, rate_limit_env, monkeypatch):
        from office_agent.api.core.config import settings

        monkeypatch.setattr(settings, "trusted_proxies", ["10.0.0.0/8"])
        app = rate_limit_env(_downstream())
        for _ in range(2):
            assert _asgi_call(app, "/api/file/upload",
                              headers={"X-Forwarded-For": "1.1.1.1"},
                              )["status"] == 200
        # 同一代理后的另一个客户端有独立配额
        assert _asgi_call(app, "/api/file/upload",
                          headers={"X-Forwarded-For": "2.2.2.2"},
                          )["status"] == 200
        # 第一个客户端已超限
        assert _asgi_call(app, "/api/file/upload",
                          headers={"X-Forwarded-For": "1.1.1.1"},
                          )["status"] == 429

    def test_spoofed_xff_does_not_bypass_without_trusted_proxy(
            self, rate_limit_env, monkeypatch):
        from office_agent.api.core.config import settings

        monkeypatch.setattr(settings, "trusted_proxies", [])
        app = rate_limit_env(_downstream())
        for _ in range(2):
            assert _asgi_call(app, "/api/file/upload")["status"] == 200
        # 伪造 XFF 不生效：peer 不可信时仍按 peer 计数
        assert _asgi_call(app, "/api/file/upload",
                          headers={"X-Forwarded-For": "9.9.9.9"},
                          )["status"] == 429


# ---------------------------------------------------------------
# 审计捕获工具
# ---------------------------------------------------------------

class _BrokenSessionFactory:
    """模拟审计存储故障：任何写库尝试直接抛错。"""

    def __call__(self):
        raise RuntimeError("audit db unavailable")


def _capture_logger(monkeypatch):
    from office_agent.security import audit as audit_module

    logger = audit_module.AuditLogger(
        enable=True, session_factory=_BrokenSessionFactory())
    monkeypatch.setattr(audit_module, "get_audit_logger", lambda: logger)
    return logger


def _task_session_factory(tmp_path):
    from office_agent.database.base import Base
    from office_agent.database import models  # noqa: F401 注册全部表

    engine = create_engine(f"sqlite:///{tmp_path / 'audit-tasks.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


# ---------------------------------------------------------------
# 任务终态审计
# ---------------------------------------------------------------

class TestTaskTransitionAudit:
    def _worker_and_task(self, tmp_path):
        from office_agent.database.repository import TaskRepository
        from office_agent.task_queue.worker import LocalWorker

        factory = _task_session_factory(tmp_path)
        session = factory()
        task = TaskRepository(session).create_task(
            "word", "生成月报", agent_name="word", user_id="user-1")
        session.commit()
        session.close()
        worker = LocalWorker.__new__(LocalWorker)
        worker._session_factory = factory
        return worker, task.id, factory

    def test_success_transition_is_audited(self, tmp_path, monkeypatch):
        audit = _capture_logger(monkeypatch)
        worker, task_id, _ = self._worker_and_task(tmp_path)
        worker._update_status(task_id, "success", duration_ms=120)
        entries = audit.get_entries(action="task_success")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.resource == "task" and entry.resource_id == task_id
        assert entry.user_id == "user-1"
        assert entry.details["task_type"] == "word"
        assert entry.details["agent"] == "word"
        assert entry.details["duration_ms"] == 120

    def test_failure_transition_is_audited_with_warning(self, tmp_path,
                                                        monkeypatch):
        audit = _capture_logger(monkeypatch)
        worker, task_id, _ = self._worker_and_task(tmp_path)
        worker._update_status(task_id, "failed", error="模型超时", duration_ms=5)
        entries = audit.get_entries(action="task_failed")
        assert len(entries) == 1
        assert entries[0].risk_level == "warning"
        assert entries[0].details["error"] == "模型超时"

    def test_cancel_transition_is_audited_once(self, tmp_path, monkeypatch):
        audit = _capture_logger(monkeypatch)
        worker, task_id, _ = self._worker_and_task(tmp_path)
        worker._update_status(task_id, "cancelled")
        # 终态守卫：重复回调不重复审计
        worker._update_status(task_id, "cancelled")
        worker._update_status(task_id, "failed", error="迟到的失败")
        entries = audit.get_entries(action="task_cancelled")
        assert len(entries) == 1
        assert audit.get_entries(action="task_failed") == []

    def test_audit_storage_failure_does_not_change_business(
            self, tmp_path, monkeypatch):
        """审计写库失败（_BrokenSessionFactory）不改变任务终态落库。"""
        _capture_logger(monkeypatch)
        worker, task_id, factory = self._worker_and_task(tmp_path)
        worker._update_status(task_id, "success", duration_ms=1)
        session = factory()
        from office_agent.database.repository import TaskRepository
        assert TaskRepository(session).get_by_id(task_id).status == "success"
        session.close()

    def test_totally_broken_audit_still_fail_open(self, tmp_path,
                                                  monkeypatch):
        """审计 logger 整体抛错时任务状态更新仍正常完成。"""
        from office_agent.security import audit as audit_module

        class _Exploding:
            def log_task_transition(self, *args, **kwargs):
                raise RuntimeError("audit subsystem down")

        monkeypatch.setattr(
            audit_module, "get_audit_logger", lambda: _Exploding())
        worker, task_id, factory = self._worker_and_task(tmp_path)
        worker._update_status(task_id, "success")
        session = factory()
        from office_agent.database.repository import TaskRepository
        assert TaskRepository(session).get_by_id(task_id).status == "success"
        session.close()

    def test_audit_failure_has_structured_fallback_log(self, tmp_path,
                                                       monkeypatch):
        """审计写库失败必须留下结构化 fallback 日志（不依赖日志系统配置）。"""
        from office_agent.security import audit as audit_module

        _capture_logger(monkeypatch)
        calls = []

        class _RecordingLogger:
            def exception(self, message, *args, **kwargs):
                calls.append(message)

            def warning(self, *args, **kwargs):
                pass

            def info(self, *args, **kwargs):
                pass

            def debug(self, *args, **kwargs):
                pass

            def error(self, *args, **kwargs):
                pass

        monkeypatch.setattr(audit_module, "logger", _RecordingLogger())
        worker, task_id, _ = self._worker_and_task(tmp_path)
        worker._update_status(task_id, "success")
        assert "Audit persistence failed" in calls


# ---------------------------------------------------------------
# 模型调用审计
# ---------------------------------------------------------------

class _FakeModelConfig:
    def __init__(self, model_id):
        self.id = model_id
        self.enabled = True
        self.api_key = "sk-secret-must-not-leak"
        self.display_name = model_id


class _FakeManager:
    def __init__(self, client):
        self._client = client

    def get_routing(self, task_type):
        return ["m1"]

    def get_model(self, model_id):
        return _FakeModelConfig(model_id)

    def get_client(self, model_id):
        return self._client


def _run_failover(client, monkeypatch, cancel_event=None):
    from office_agent.model_gateway.failover import FailoverManager
    from office_agent.models.model_schemas import AITaskType

    audit = _capture_logger(monkeypatch)
    manager = FailoverManager(_FakeManager(client),
                              max_retries=1, retry_delay=0)
    response = manager.execute_with_failover(
        AITaskType.SIMPLE_TEXT,
        lambda c, **kw: c.chat(),
        cancel_event=cancel_event,
    )
    return audit, response


class TestModelCallAudit:
    def test_success_call_audited_with_safe_metadata(self, monkeypatch):
        from office_agent.models.model_schemas import ModelResponse

        class _Ok:
            def chat(self):
                return ModelResponse(success=True, model_used="m1",
                                     provider="prov-x")

        audit, response = _run_failover(_Ok(), monkeypatch)
        assert response.success
        entries = audit.get_entries(action="model_call")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.status == "success"
        assert entry.resource == "model:m1"
        assert entry.details["provider"] == "prov-x"
        assert entry.details["task_type"] == "simple_text"
        assert entry.details["attempts"] == 1
        assert "duration_ms" in entry.details

    def test_failed_call_audited_with_normalized_category(self, monkeypatch):
        from office_agent.models.model_schemas import ModelResponse

        class _RateLimited:
            def chat(self):
                return ModelResponse(success=False,
                                     error="HTTP 429 rate limit exceeded")

        audit, response = _run_failover(_RateLimited(), monkeypatch)
        assert not response.success
        entries = audit.get_entries(action="model_call")
        assert len(entries) == 1
        assert entries[0].status == "error"
        assert entries[0].risk_level == "warning"
        assert entries[0].details["failure_category"] == "rate_limit"

    def test_timeout_category(self, monkeypatch):
        from office_agent.models.model_schemas import ModelResponse

        class _Timeout:
            def chat(self):
                return ModelResponse(success=False, error="read timed out")

        audit, _ = _run_failover(_Timeout(), monkeypatch)
        entries = audit.get_entries(action="model_call")
        assert entries[0].details["failure_category"] == "timeout"

    def test_cancelled_call_audited_as_cancelled(self, monkeypatch):
        event = threading.Event()
        event.set()
        audit, response = _run_failover(None, monkeypatch, cancel_event=event)
        assert not response.success
        entries = audit.get_entries(action="model_call")
        assert len(entries) == 1
        assert entries[0].status == "cancelled"

    def test_secrets_never_enter_audit(self, monkeypatch):
        from office_agent.models.model_schemas import ModelResponse
        from office_agent.security.audit import AuditLogger

        class _Ok:
            def chat(self):
                return ModelResponse(success=True, model_used="m1")

        audit, _ = _run_failover(_Ok(), monkeypatch)
        entry = audit.get_entries(action="model_call")[0]
        assert "sk-secret" not in str(entry.to_dict())
        # helper 主动剥离常见敏感键
        stripped = AuditLogger(enable=False).log_model_call(
            "success", model_id="m1",
            details={"prompt": "机密提示词", "api_key": "sk-x",
                     "duration_ms": 3})
        assert "prompt" not in stripped.details
        assert "api_key" not in stripped.details
        assert stripped.details["duration_ms"] == 3

    def test_correlation_ids_from_context(self, monkeypatch):
        from office_agent.logging_system import context
        from office_agent.models.model_schemas import ModelResponse

        class _Ok:
            def chat(self):
                return ModelResponse(success=True, model_used="m1")

        tokens = [
            context.set_task_id("task-42"),
            context.set_request_id("req-42"),
            context.set_user_id("user-42"),
        ]
        try:
            audit, _ = _run_failover(_Ok(), monkeypatch)
        finally:
            context._task_id_var.reset(tokens[0])
            context._request_id_var.reset(tokens[1])
            context._user_id_var.reset(tokens[2])
        entry = audit.get_entries(action="model_call")[0]
        assert entry.details["task_id"] == "task-42"
        assert entry.details["request_id"] == "req-42"
        assert entry.user_id == "user-42"

    def test_audit_failure_does_not_change_model_result(self, monkeypatch):
        """审计存储失败（_BrokenSessionFactory）时模型调用结果不受影响。"""
        from office_agent.models.model_schemas import ModelResponse

        class _Ok:
            def chat(self):
                return ModelResponse(success=True, model_used="m1")

        _, response = _run_failover(_Ok(), monkeypatch)
        assert response.success
