"""统一 API 错误响应契约（清单 3.5 异常响应契约）专项测试。

根因背景：
- 修复前，裸 ``HTTPException`` 只产出 ``HTTP_<status>`` 临时代码，
  各 API 的失败错误码不成体系；
- 存储（OSError 系）、数据库（SQLAlchemyError）、第三方上游
  （urllib URLError / TimeoutError / ConnectionError）等基础设施
  异常直接落入 generic 500 INTERNAL_ERROR，既不分类也可能泄漏
  内部细节（SQL、路径）。

本文件钉住契约：
- envelope 恒为 {success, error_code, message, details, timestamp}；
- HTTP 状态 -> 稳定错误码映射（400/401/403/404/409/413/415/422/429/5xx）；
- 领域异常 -> DATABASE_ERROR / FILE_NOT_FOUND / STORAGE_ERROR /
  UPSTREAM_ERROR / UPSTREAM_TIMEOUT，message 为安全通用文案；
- APIError 业务层级（ModelError 等）行为不变；
- ErrorHandlingMiddleware 与 handler 的 500 共用同一 envelope 构造器。
"""
from urllib.error import URLError

import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from starlette.testclient import TestClient

from office_agent.api.core.error_contract import (
    DOMAIN_ERROR_MAPPINGS,
    ERROR_CODE_BY_STATUS,
    build_error_envelope,
    domain_error_response_args,
    error_code_for_status,
)
from office_agent.api.core.exceptions import APIError, ModelError, TaskNotFoundError
from office_agent.api.core.handlers import register_exception_handlers


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise/{kind}")
    async def raise_kind(kind: str):
        if kind == "http404":
            raise HTTPException(status_code=404, detail="资源不存在")
        if kind == "http409":
            raise HTTPException(status_code=409, detail="状态冲突")
        if kind == "http413":
            raise HTTPException(status_code=413, detail="上传内容超过大小限制")
        if kind == "api_error":
            raise TaskNotFoundError("任务不存在: t-1")
        if kind == "model_error":
            raise ModelError("模型调用失败: 余额不足")
        if kind == "db":
            raise SQLAlchemyError("INSERT INTO secret_table ... password=...")
        if kind == "file_missing":
            raise FileNotFoundError("文件不存在: /data/private/x.docx")
        if kind == "os_error":
            raise OSError(28, "No space left on device /data/private")
        if kind == "url_error":
            raise URLError("connection refused by upstream.model.internal")
        if kind == "timeout":
            raise TimeoutError("upstream read timed out")
        if kind == "conn_error":
            raise ConnectionError("upstream reset")
        if kind == "unknown":
            raise RuntimeError("boom")
        return {"ok": True}

    class _Payload(BaseModel):
        count: int

    @app.post("/validated")
    async def validated(payload: _Payload):
        return {"ok": True}

    return app


@pytest.fixture()
def client():
    return TestClient(_build_app())


def _assert_envelope(payload, error_code):
    assert payload["success"] is False
    assert payload["error_code"] == error_code
    assert isinstance(payload["message"], str) and payload["message"]
    assert "details" in payload
    assert isinstance(payload["timestamp"], str) and payload["timestamp"]


# ---------------------------------------------------------------
# envelope 构造器与映射表
# ---------------------------------------------------------------

def test_envelope_builder_shape():
    env = build_error_envelope("X_CODE", "msg")
    assert set(env) == {"success", "error_code", "message", "details", "timestamp"}
    assert env["success"] is False and env["details"] is None


def test_status_code_mapping_covers_common_failures():
    expected = {
        400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN",
        404: "NOT_FOUND", 409: "CONFLICT", 413: "PAYLOAD_TOO_LARGE",
        415: "UNSUPPORTED_MEDIA_TYPE", 422: "VALIDATION_ERROR",
        429: "RATE_LIMITED", 500: "INTERNAL_ERROR",
        502: "UPSTREAM_ERROR", 503: "SERVICE_UNAVAILABLE",
        504: "UPSTREAM_TIMEOUT",
    }
    for status, code in expected.items():
        assert error_code_for_status(status) == code
        assert ERROR_CODE_BY_STATUS[status] == code
    # 未收录状态保留 HTTP_<code> 兜底
    assert error_code_for_status(418) == "HTTP_418"


def test_domain_mapping_specificity_and_coverage():
    # FileNotFoundError 命中自身映射而非 OSError（MRO 精度）
    assert domain_error_response_args(FileNotFoundError())[1] == "FILE_NOT_FOUND"
    assert domain_error_response_args(OSError())[1] == "STORAGE_ERROR"
    assert domain_error_response_args(SQLAlchemyError())[1] == "DATABASE_ERROR"
    assert domain_error_response_args(URLError("x"))[1] == "UPSTREAM_ERROR"
    assert domain_error_response_args(TimeoutError())[1] == "UPSTREAM_TIMEOUT"
    assert domain_error_response_args(ConnectionError())[1] == "UPSTREAM_ERROR"
    # 普通业务异常不属于领域映射
    assert domain_error_response_args(RuntimeError()) is None
    assert domain_error_response_args(ValueError("bad input")) is None
    # 映射表无重复异常类型
    types = [m[0] for m in DOMAIN_ERROR_MAPPINGS]
    assert len(types) == len(set(types))


# ---------------------------------------------------------------
# HTTP 层契约
# ---------------------------------------------------------------

def test_http_exception_uses_stable_codes(client):
    resp = client.get("/raise/http404")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), "NOT_FOUND")
    assert resp.json()["message"] == "资源不存在"

    resp = client.get("/raise/http409")
    assert resp.status_code == 409
    _assert_envelope(resp.json(), "CONFLICT")

    resp = client.get("/raise/http413")
    assert resp.status_code == 413
    _assert_envelope(resp.json(), "PAYLOAD_TOO_LARGE")


def test_validation_error_contract(client):
    resp = client.post("/validated", json={"count": "not-an-int"})
    assert resp.status_code == 422
    payload = resp.json()
    _assert_envelope(payload, "VALIDATION_ERROR")
    assert payload["details"][0]["field"].endswith("count")


def test_api_error_business_hierarchy_unchanged(client):
    resp = client.get("/raise/api_error")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), "TASK_NOT_FOUND")
    assert "任务不存在" in resp.json()["message"]

    # 第三方模型错误业务码：502 MODEL_ERROR
    resp = client.get("/raise/model_error")
    assert resp.status_code == 502
    _assert_envelope(resp.json(), "MODEL_ERROR")


# ---------------------------------------------------------------
# 领域异常：存储 / 数据库 / 第三方上游
# ---------------------------------------------------------------

def test_database_error_contract(client):
    resp = client.get("/raise/db")
    assert resp.status_code == 500
    payload = resp.json()
    _assert_envelope(payload, "DATABASE_ERROR")
    assert payload["message"] == "数据库服务暂时不可用"
    # 非 debug 模式不得泄漏 SQL 细节
    assert "secret_table" not in str(payload)
    assert payload["details"] is None


def test_storage_error_contract(client):
    resp = client.get("/raise/file_missing")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), "FILE_NOT_FOUND")
    assert "/data/private" not in str(resp.json())

    resp = client.get("/raise/os_error")
    assert resp.status_code == 500
    _assert_envelope(resp.json(), "STORAGE_ERROR")
    assert resp.json()["message"] == "存储服务暂时不可用"


def test_upstream_error_contract(client):
    for kind, status, code in [
        ("url_error", 502, "UPSTREAM_ERROR"),
        ("conn_error", 502, "UPSTREAM_ERROR"),
        ("timeout", 504, "UPSTREAM_TIMEOUT"),
    ]:
        resp = client.get(f"/raise/{kind}")
        assert resp.status_code == status, kind
        payload = resp.json()
        _assert_envelope(payload, code)
        # 不泄漏上游主机名等内部细节
        assert "upstream.model.internal" not in str(payload)


def test_unknown_error_still_internal():
    # generic Exception handler 挂在 ServerErrorMiddleware 上，响应后
    # 仍会 re-raise 供服务器记录；TestClient 需关闭 raise_server_exceptions
    client = TestClient(_build_app(), raise_server_exceptions=False)
    resp = client.get("/raise/unknown")
    assert resp.status_code == 500
    _assert_envelope(resp.json(), "INTERNAL_ERROR")
    assert resp.json()["message"] == "服务器内部错误"


def test_middleware_500_uses_same_envelope_builder():
    """ErrorHandlingMiddleware 的 500 与 handler 共用构造器。"""
    import inspect

    from office_agent.logging_system import middleware as mw

    source = inspect.getsource(mw.ErrorHandlingMiddleware.dispatch)
    assert "build_error_envelope" in source
    assert '"error_code": "INTERNAL_ERROR"' not in source


def test_api_error_subclass_defaults_intact():
    """APIError 层级既有语义不被契约层改变。"""
    err = APIError("x")
    assert err.error_code == "API_ERROR" and err.status_code == 500
    assert ModelError("m").status_code == 502
    assert ModelError("m").error_code == "MODEL_ERROR"
