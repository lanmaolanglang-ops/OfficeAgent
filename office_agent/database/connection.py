"""
数据库连接配置

默认使用 SQLite（零配置），可通过 DATABASE_URL 切换到 PostgreSQL：
    postgresql://user:password@localhost/office_agent
"""
import os
from sqlalchemy import create_engine, Engine, event, inspect, text
from ..runtime_config import get_data_root

# 默认数据目录（桌面启动器通过 OFFICE_AGENT_DATA_DIR 重定向到 %APPDATA%/OfficeAgent）
DATA_DIR = get_data_root() / "db"

# 默认 SQLite 路径
DEFAULT_DB_PATH = DATA_DIR / "office_agent.db"

# 数据库 URL（优先环境变量，否则 SQLite）
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{DEFAULT_DB_PATH}"
)

# SQLite busy timeout（秒）：多线程（worker 线程池 + 请求线程 + 日志线程）并发写时，
# 写锁冲突的等待上限。默认 5 秒在 8 个并发写者下会频繁抛 "database is locked"。
_SQLITE_BUSY_TIMEOUT = int(os.environ.get("DB_BUSY_TIMEOUT", "30"))


def _register_sqlite_pragmas(target: Engine) -> Engine:
    """为 SQLite 引擎注册连接级 PRAGMA。

    - journal_mode=WAL：读写不互斥，多线程并发写大幅减少锁冲突
    - synchronous=NORMAL：WAL 推荐档位，应用崩溃不丢事务
    """

    @event.listens_for(target, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        # :memory: 库不支持 WAL，网络盘上 WAL 可能失败——pragma 失败不阻塞启动
        try:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()
        except Exception:
            pass
    return target


# 引擎参数
_engine_kwargs = {
    "echo": os.environ.get("DB_ECHO", "false").lower() == "true",
    "future": True,
}

# SQLite 需要额外参数
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {
        "check_same_thread": False,
        "timeout": _SQLITE_BUSY_TIMEOUT,
    }
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
    if db_url.startswith("sqlite") and db_url != "sqlite:///:memory:":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    kwargs = dict(_engine_kwargs)
    if db_url.startswith("sqlite"):
        for pool_option in (
            "pool_size", "max_overflow", "pool_timeout", "pool_recycle"
        ):
            kwargs.pop(pool_option, None)
        kwargs["connect_args"] = {
            "check_same_thread": False,
            "timeout": _SQLITE_BUSY_TIMEOUT,
        }
    else:
        # ``_engine_kwargs`` is initialized for the process-wide default URL.
        # A caller may explicitly request another dialect, so never leak
        # SQLite-only arguments into PostgreSQL (or vice versa).
        kwargs.pop("connect_args", None)
        kwargs.setdefault("pool_size", int(os.environ.get("DB_POOL_SIZE", "10")))
        kwargs.setdefault("max_overflow", int(os.environ.get("DB_MAX_OVERFLOW", "20")))
        kwargs.setdefault("pool_timeout", int(os.environ.get("DB_POOL_TIMEOUT", "30")))
        kwargs.setdefault("pool_recycle", int(os.environ.get("DB_POOL_RECYCLE", "3600")))
    engine_obj = create_engine(db_url, **kwargs)
    if db_url.startswith("sqlite"):
        _register_sqlite_pragmas(engine_obj)
    return engine_obj


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
        file_columns = {column["name"] for column in inspector.get_columns("file")}
        user_columns = {column["name"] for column in inspector.get_columns("user")}
        missing_user_columns = {
            "is_verified": "BOOLEAN NOT NULL DEFAULT 0",
            "last_login_ip": "VARCHAR(64)",
            "failed_login_count": "INTEGER NOT NULL DEFAULT 0",
            "locked_until": "DATETIME",
            "extra": "JSON",
        }
        with engine.begin() as connection:
            for column_name, column_definition in missing_task_columns.items():
                if column_name not in task_columns:
                    connection.execute(text(
                        f"ALTER TABLE task ADD COLUMN {column_name} {column_definition}"
                    ))
            if "deleted_at" not in file_columns:
                connection.execute(text("ALTER TABLE file ADD COLUMN deleted_at DATETIME"))
            for column_name, column_definition in missing_user_columns.items():
                if column_name not in user_columns:
                    connection.execute(text(
                        f'ALTER TABLE "user" ADD COLUMN {column_name} {column_definition}'
                    ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_file_deleted_at ON file (deleted_at)"
            ))
    return engine
