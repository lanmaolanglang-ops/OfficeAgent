"""
数据库 Session 管理
"""
import threading
from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session, sessionmaker

from .connection import default_engine

_session_kwargs = {
    "autocommit": False,
    "autoflush": False,
    "expire_on_commit": False,
    "future": True,
}

_factory_lock = threading.Lock()
_factory: sessionmaker = None


def _session_factory() -> sessionmaker:
    """惰性创建 sessionmaker（首次使用时才绑定默认引擎）。

    import 本模块不再触发目录创建或 engine 初始化。
    """
    global _factory
    if _factory is None:
        with _factory_lock:
            if _factory is None:
                _factory = sessionmaker(bind=default_engine(), **_session_kwargs)
    return _factory


def reset_session_factory():
    """解除当前 Session 工厂绑定（dispose_default_engine 时联动调用）。

    下次使用时重新绑定新的默认引擎。
    """
    global _factory
    with _factory_lock:
        _factory = None


class _LazySessionFactory:
    """``SessionLocal`` 兼容代理。

    保持 ``SessionLocal()`` 调用语义与可整体 monkeypatch 的模块属性形态，
    但把 engine 绑定推迟到首次真正创建 Session 时。
    """

    def __call__(self, **kwargs) -> Session:
        return _session_factory()(**kwargs)


# Session 工厂（惰性绑定；调用方一律通过 ``SessionLocal()`` 使用）
SessionLocal = _LazySessionFactory()


def get_session() -> Session:
    """获取新 Session（手动管理）"""
    return SessionLocal()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """事务上下文管理器

    用法：
        with session_scope() as session:
            repo = UserRepository(session)
            repo.create(...)
        # 自动提交/回滚
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    """FastAPI 依赖注入用"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
