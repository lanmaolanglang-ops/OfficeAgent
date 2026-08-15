"""
数据库 Session 管理
"""
from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session, sessionmaker

from .connection import engine

# Session 工厂
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)


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
