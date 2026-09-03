"""Shared FastAPI dependencies for the API layer."""
from __future__ import annotations


def get_db_session():
    """Yield a SQLAlchemy session and always close it.

    统一的 DB 会话依赖注入入口：端点通过 ``Depends(get_db_session)`` 获取会话，
    由 FastAPI 保证请求结束后关闭；测试可用 ``app.dependency_overrides`` 替换。
    延迟导入 SessionLocal，避免在无需数据库的场景（如纯健康检查）提前初始化。
    """
    from ..database.session import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
