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
from .connection import engine, init_db, DATABASE_URL
from .session import SessionLocal, session_scope, get_db
from .base import Base
from .._version import __version__

__all__ = [
    "__version__",
    "engine", "init_db", "DATABASE_URL",
    "SessionLocal", "session_scope", "get_db",
    "Base",
]
