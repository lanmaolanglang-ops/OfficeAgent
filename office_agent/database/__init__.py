"""
Office Agent Database Layer

SQLAlchemy 2.0 + Alembic 数据持久化层。

使用示例：
    from office_agent.database.session import session_scope
    from office_agent.database.repository import TaskRepository, FileRepository

    with session_scope() as session:
        task_repo = TaskRepository(session)
        task = task_repo.create_task(
            task_type="word_format",
            instruction="排版论文",
            agent_name="word_agent",
        )
        print(task.id)
"""
from .connection import (
    DATABASE_URL,
    default_engine,
    dispose_default_engine,
    init_db,
)
from .session import SessionLocal, session_scope, get_db
from .base import Base
from .runtime_migrations import migration_head, upgrade_database
from .._version import __version__


def __getattr__(name: str):
    # 兼容 ``from office_agent.database import engine``：首次访问时才创建引擎，
    # import 数据库包不再产生目录创建 / engine 初始化副作用。
    if name == "engine":
        return default_engine()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "__version__",
    "engine", "init_db", "DATABASE_URL",
    "default_engine", "dispose_default_engine",
    "migration_head", "upgrade_database",
    "SessionLocal", "session_scope", "get_db",
    "Base",
]
