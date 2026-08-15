"""
数据库连接配置

默认使用 SQLite（零配置），可通过 DATABASE_URL 切换到 PostgreSQL：
    postgresql://user:password@localhost/office_agent
"""
import os
from pathlib import Path
from sqlalchemy import create_engine, Engine, inspect, text

# 默认数据目录
DATA_DIR = Path(os.path.expanduser("~/.office_agent/db"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 默认 SQLite 路径
DEFAULT_DB_PATH = DATA_DIR / "office_agent.db"

# 数据库 URL（优先环境变量，否则 SQLite）
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{DEFAULT_DB_PATH}"
)

# 引擎参数
_engine_kwargs = {
    "echo": os.environ.get("DB_ECHO", "false").lower() == "true",
    "future": True,
}

# SQLite 需要额外参数
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # PostgreSQL 连接池配置
    _engine_kwargs.update({
        "pool_size": int(os.environ.get("DB_POOL_SIZE", "10")),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "20")),
        "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", "30")),
        "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", "3600")),
    })


def get_engine(url: str = None) -> Engine:
    """获取数据库引擎"""
    db_url = url or DATABASE_URL
    kwargs = dict(_engine_kwargs)
    if db_url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    return create_engine(db_url, **kwargs)


# 全局引擎
engine = get_engine()


def init_db(drop_all: bool = False):
    """初始化数据库（创建所有表）"""
    from .base import Base
    # 导入所有模型以注册到 Base.metadata
    from . import models  # noqa: F401

    if drop_all:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    # ``create_all`` does not alter an existing table.  The desktop build can
    # therefore open a database created by an earlier release and still fail
    # as soon as a repository selects a newly-added ORM column.  Keep the
    # lightweight SQLite migration here as a startup safety net; Alembic can
    # still be used for full production migrations.
    if engine.dialect.name == "sqlite":
        inspector = inspect(engine)
        task_columns = {column["name"] for column in inspector.get_columns("task")}
        missing_task_columns = {
            "parent_task_id": "VARCHAR(32)",
            "revision_number": "INTEGER NOT NULL DEFAULT 1",
        }
        with engine.begin() as connection:
            for column_name, column_definition in missing_task_columns.items():
                if column_name not in task_columns:
                    connection.execute(text(
                        f"ALTER TABLE task ADD COLUMN {column_name} {column_definition}"
                    ))
    return engine
