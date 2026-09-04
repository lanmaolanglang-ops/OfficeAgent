"""分页参数的统一输入契约。

所有公开 API 的 ``limit/offset/page/page_size`` 约束语义在此单点定义，
路由不得再各自书写 ``Query(..., ge=1, le=...)``：

- ``limit`` / ``page_size`` 必须 >= 1，且不超过端点声明的业务上限；
- 业务上限可以因端点而异，但不得超过全局硬上限 ``MAX_LIMIT``
  （与 Repository 防御层 ``BaseRepository._page`` 共用同一常量）；
- ``offset`` 必须 >= 0；``page`` 必须 >= 1；
- 越界请求统一由 FastAPI 校验层产出 422，经
  ``api/core/handlers.py`` 落入 ``VALIDATION_ERROR`` 错误契约，
  各端点错误结构一致，不出现 500。

非 FastAPI 层（如内存 TaskManager）使用 ``validate_page`` 获得同语义
的防御性校验。
"""
from typing import Any

from fastapi import Query

from ...database.repository.base import MAX_LIMIT

DEFAULT_LIMIT = 100
DEFAULT_PAGE_SIZE = 20
DEFAULT_PAGE_SIZE_MAX = 200


def _check_declared_bounds(default: int, maximum: int) -> None:
    if not 1 <= default <= maximum <= MAX_LIMIT:
        raise ValueError(
            "分页参数声明非法: "
            f"default={default}, maximum={maximum}, 硬上限={MAX_LIMIT}"
        )


def limit_query(default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> Any:
    """统一 limit 查询参数：``1 <= limit <= maximum <= MAX_LIMIT``。"""
    _check_declared_bounds(default, maximum)
    return Query(default=default, ge=1, le=maximum,
                 description=f"返回条数上限（1~{maximum}）")


def offset_query() -> Any:
    """统一 offset 查询参数：``offset >= 0``。"""
    return Query(default=0, ge=0, description="跳过的记录数（>= 0）")


def page_query() -> Any:
    """统一 page 查询参数：``page >= 1``。"""
    return Query(default=1, ge=1, description="页码（>= 1）")


def page_size_query(default: int = DEFAULT_PAGE_SIZE,
                    maximum: int = DEFAULT_PAGE_SIZE_MAX) -> Any:
    """统一 page_size 查询参数：``1 <= page_size <= maximum <= MAX_LIMIT``。"""
    _check_declared_bounds(default, maximum)
    return Query(default=default, ge=1, le=maximum,
                 description=f"每页条数（1~{maximum}）")


def validate_page(page: int, page_size: int,
                  maximum: int = MAX_LIMIT) -> None:
    """非 FastAPI 层的分页防御校验，语义与 Query 工厂一致。"""
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page 必须是 >= 1 的整数")
    if (isinstance(page_size, bool) or not isinstance(page_size, int)
            or not 1 <= page_size <= maximum):
        raise ValueError(f"page_size 必须在 1 到 {maximum} 之间")
