"""分页 / limit / offset 统一输入契约专项测试（清单：分页校验统一）。

钉住的规则：
- 所有公开 API 的分页参数经 ``api/core/pagination.py`` 共享工厂声明，
  语义统一：limit/page_size >= 1 且有上限、offset >= 0、page >= 1；
- 业务上限可因端点而异，但不得超过 Repository 硬上限 MAX_LIMIT；
- 越界请求统一返回 422 + VALIDATION_ERROR 错误契约，不出现 500；
- Repository 防御层对裸 limit/offset 同样拒绝负数与超上限；
- 内存 TaskManager 与 API 同语义校验 page/page_size；
- 真实路由（main.py / router/file.py / router/task.py）必须经共享工厂
  声明分页参数，不得回退为各自书写 Query(ge=..., le=...)。
"""
import inspect
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from office_agent.api.core.pagination import (
    DEFAULT_LIMIT,
    DEFAULT_PAGE_SIZE,
    DEFAULT_PAGE_SIZE_MAX,
    limit_query,
    offset_query,
    page_query,
    page_size_query,
    validate_page,
)
from office_agent.api.core.handlers import register_exception_handlers
from office_agent.database.repository.base import MAX_LIMIT

SRC = Path(__file__).resolve().parents[2] / "office_agent"


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/items")
    def items(limit: int = limit_query(),
              offset: int = offset_query()):
        return {"limit": limit, "offset": offset}

    @app.get("/paged")
    def paged(page: int = page_query(),
              page_size: int = page_size_query()):
        return {"page": page, "page_size": page_size}

    return app


@pytest.fixture()
def client():
    return TestClient(_build_app())


def _assert_validation_envelope(response):
    assert response.status_code == 422
    payload = response.json()
    assert payload["success"] is False
    assert payload["error_code"] == "VALIDATION_ERROR"
    assert isinstance(payload["message"], str) and payload["message"]
    assert isinstance(payload["timestamp"], str) and payload["timestamp"]


def _bounds(param):
    """FastAPI 0.141 将 Query 约束存于 annotated metadata。"""
    ge = le = None
    for meta in getattr(param, "metadata", []):
        if getattr(meta, "ge", None) is not None:
            ge = meta.ge
        if getattr(meta, "le", None) is not None:
            le = meta.le
    return ge, le


# ---------------------------------------------------------------
# 共享工厂声明语义
# ---------------------------------------------------------------

def test_limit_query_bounds_and_default():
    param = limit_query()
    assert param.default == DEFAULT_LIMIT
    assert _bounds(param) == (1, MAX_LIMIT)


def test_offset_and_page_query_bounds():
    assert _bounds(offset_query())[0] == 0
    page = page_query()
    assert page.default == 1 and _bounds(page)[0] == 1


def test_page_size_query_defaults():
    param = page_size_query()
    assert param.default == DEFAULT_PAGE_SIZE
    assert _bounds(param) == (1, DEFAULT_PAGE_SIZE_MAX)


def test_declared_maximum_cannot_exceed_hard_limit():
    with pytest.raises(ValueError, match="硬上限"):
        limit_query(maximum=MAX_LIMIT + 1)
    with pytest.raises(ValueError, match="硬上限"):
        page_size_query(maximum=MAX_LIMIT + 1)
    with pytest.raises(ValueError):
        limit_query(default=0)


# ---------------------------------------------------------------
# limit 边界（经统一错误契约）
# ---------------------------------------------------------------

def test_limit_minimum_and_maximum_accepted(client):
    assert client.get("/items", params={"limit": 1}).status_code == 200
    response = client.get("/items", params={"limit": MAX_LIMIT})
    assert response.status_code == 200
    assert response.json()["limit"] == MAX_LIMIT


def test_limit_zero_and_negative_rejected(client):
    _assert_validation_envelope(client.get("/items", params={"limit": 0}))
    _assert_validation_envelope(client.get("/items", params={"limit": -5}))


def test_limit_above_maximum_rejected(client):
    _assert_validation_envelope(
        client.get("/items", params={"limit": MAX_LIMIT + 1}))


# ---------------------------------------------------------------
# offset 边界
# ---------------------------------------------------------------

def test_offset_zero_and_positive_accepted(client):
    assert client.get("/items", params={"offset": 0}).status_code == 200
    response = client.get("/items", params={"offset": 5000})
    assert response.status_code == 200
    assert response.json()["offset"] == 5000


def test_offset_negative_rejected(client):
    _assert_validation_envelope(client.get("/items", params={"offset": -1}))


# ---------------------------------------------------------------
# page / page_size 边界
# ---------------------------------------------------------------

def test_page_one_accepted_page_zero_rejected(client):
    assert client.get("/paged", params={"page": 1}).status_code == 200
    _assert_validation_envelope(client.get("/paged", params={"page": 0}))
    _assert_validation_envelope(client.get("/paged", params={"page": -2}))


def test_page_size_bounds(client):
    assert client.get("/paged", params={"page_size": 1}).status_code == 200
    assert client.get(
        "/paged", params={"page_size": DEFAULT_PAGE_SIZE_MAX}).status_code == 200
    _assert_validation_envelope(client.get("/paged", params={"page_size": 0}))
    _assert_validation_envelope(
        client.get("/paged", params={"page_size": DEFAULT_PAGE_SIZE_MAX + 1}))


def test_invalid_params_never_produce_500(client):
    for params in ({"limit": 0}, {"limit": -1}, {"limit": 10 ** 9},
                   {"offset": -1}, {"limit": "abc"}):
        response = client.get("/items", params=params)
        assert response.status_code == 422
    response = client.get("/paged", params={"page": 0, "page_size": 0})
    assert response.status_code == 422


# ---------------------------------------------------------------
# 非 FastAPI 层的同语义防御校验
# ---------------------------------------------------------------

def test_validate_page_core_layer():
    validate_page(1, 20)
    validate_page(3, MAX_LIMIT)
    with pytest.raises(ValueError, match="page"):
        validate_page(0, 20)
    with pytest.raises(ValueError, match="page"):
        validate_page(-1, 20)
    with pytest.raises(ValueError, match="page_size"):
        validate_page(1, 0)
    with pytest.raises(ValueError, match="page_size"):
        validate_page(1, MAX_LIMIT + 1)


def test_task_manager_list_tasks_validates_page():
    from office_agent.api.core.task_manager import TaskManager

    manager = TaskManager()
    with pytest.raises(ValueError, match="page"):
        manager.list_tasks(page=0)
    with pytest.raises(ValueError, match="page_size"):
        manager.list_tasks(page_size=0)
    with pytest.raises(ValueError, match="page_size"):
        manager.list_tasks(page_size=MAX_LIMIT + 1)
    tasks, total = manager.list_tasks(page=1, page_size=20)
    assert tasks == [] and total == 0


# ---------------------------------------------------------------
# Repository 防御层：裸 limit/offset 同样受控
# ---------------------------------------------------------------

def test_execution_log_repos_validate_limit():
    from office_agent.database.repository.execution_repo import (
        ErrorLogRepository,
        ExecutionLogRepository,
        ModelCallLogRepository,
    )

    exec_repo = ExecutionLogRepository(None)
    model_repo = ModelCallLogRepository(None)
    error_repo = ErrorLogRepository(None)
    for bad in (0, -1, MAX_LIMIT + 1):
        with pytest.raises(ValueError, match="limit"):
            exec_repo.get_by_agent("word", limit=bad)
        with pytest.raises(ValueError, match="limit"):
            exec_repo.get_errors(limit=bad)
        with pytest.raises(ValueError, match="limit"):
            model_repo.get_by_model("gpt", limit=bad)
        with pytest.raises(ValueError, match="limit"):
            error_repo.get_recent(limit=bad)
        with pytest.raises(ValueError, match="limit"):
            error_repo.get_by_type("X", limit=bad)


def test_file_repo_validates_offset_and_limit():
    from office_agent.database.repository.file_repo import FileRepository

    repo = FileRepository(None)
    with pytest.raises(ValueError, match="offset"):
        repo.get_by_owner("u1", offset=-1)
    with pytest.raises(ValueError, match="limit"):
        repo.get_by_owner("u1", limit=0)
    with pytest.raises(ValueError, match="offset"):
        repo.get_by_type("word", offset=-1)
    with pytest.raises(ValueError, match="limit"):
        repo.get_by_type("word", limit=MAX_LIMIT + 1)


# ---------------------------------------------------------------
# 真实路由必须经共享工厂声明（防回退源码守卫）
# ---------------------------------------------------------------

def test_real_routers_use_shared_pagination_factories():
    expectations = {
        SRC / "api" / "main.py": ["limit_query"],
        SRC / "api" / "router" / "file.py": ["page_query", "page_size_query"],
        SRC / "api" / "router" / "task.py": ["page_query", "page_size_query"],
    }
    stale_patterns = [
        "Query(default=100, ge=1, le=1000)",
        "Query(default=50, ge=1, le=200)",
        "Query(default=20, ge=1, le=200)",
        "Query(default=1, ge=1)",
    ]
    for path, factories in expectations.items():
        source = path.read_text(encoding="utf-8")
        assert "pagination import" in source or "from ..core.pagination" in source \
            or "from office_agent.api.core.pagination" in source, path
        for factory in factories:
            assert factory in source, f"{path} 未使用 {factory}"
        for pattern in stale_patterns:
            assert pattern not in source, f"{path} 回退为手写 {pattern}"


def test_shared_module_is_single_source_of_bounds():
    """共享工厂与 Repository 硬上限同源，且 API 声明不得超过它。"""
    import office_agent.api.core.pagination as pagination

    assert pagination.MAX_LIMIT == MAX_LIMIT
    assert DEFAULT_PAGE_SIZE_MAX <= MAX_LIMIT
    assert DEFAULT_LIMIT <= MAX_LIMIT
    # 工厂签名不暴露 ge/le 自由参数，语义只能来自共享模块
    signature = inspect.signature(limit_query)
    assert set(signature.parameters) == {"default", "maximum"}
