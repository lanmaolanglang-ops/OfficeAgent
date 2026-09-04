"""数据库引擎显式生命周期回归。

根因：``database/connection.py`` 曾在 import 时创建数据目录并初始化全局
engine，任何 ``import office_agent.database``（包括只做静态分析的工具进程）
都会落盘副作用。修复后：

- import 阶段零副作用（不建目录、不建 engine）；
- 默认引擎收敛为线程安全惰性单例 ``default_engine()``；
- ``dispose_default_engine()`` 提供显式 shutdown 生命周期；
- ``SessionLocal`` 惰性绑定，首次真正建 Session 时才触碰引擎；
- 旧 ``from ... import engine`` 兼容路径保持可用但同样惰性。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, text


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    """把默认数据库重定向到临时目录，并保证单例前后干净。"""
    from office_agent.database import connection as connection_module
    from office_agent.database import session as session_module

    db_dir = tmp_path / "db"
    monkeypatch.setattr(connection_module, "DATA_DIR", db_dir)
    monkeypatch.setattr(
        connection_module, "DATABASE_URL", f"sqlite:///{db_dir / 'office_agent.db'}"
    )
    connection_module.dispose_default_engine()
    yield connection_module, session_module, db_dir
    connection_module.dispose_default_engine()


def test_database_package_import_has_no_side_effects(tmp_path):
    """import 数据库包不得创建目录、engine 或 session 工厂。"""
    data_root = tmp_path / "data_root"
    code = (
        "import office_agent.database; "
        "import office_agent.database.connection as c; "
        "import office_agent.database.session as s; "
        "from pathlib import Path; "
        f"root = Path({str(data_root)!r}); "
        "assert not root.exists(), 'import created the data root'; "
        "assert c._default_engine is None, 'import created the default engine'; "
        "assert s._factory is None, 'import created the session factory'"
    )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["OFFICE_AGENT_DATA_DIR"] = str(data_root)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, result.stderr


def test_default_engine_is_lazy_singleton(isolated_db):
    connection_module, _, db_dir = isolated_db
    assert connection_module._default_engine is None
    assert not db_dir.exists()

    engine_first = connection_module.default_engine()
    assert connection_module._default_engine is engine_first
    assert db_dir.is_dir()
    assert connection_module.default_engine() is engine_first


def test_legacy_engine_attribute_stays_compatible(isolated_db):
    """旧 ``from ... import engine`` / ``connection.engine`` 路径仍可用且惰性。"""
    connection_module, _, _ = isolated_db
    import office_agent.database as database_package

    singleton = connection_module.default_engine()
    assert connection_module.engine is singleton
    assert database_package.engine is singleton


def test_session_local_binds_lazily_and_sessions_work(isolated_db):
    connection_module, session_module, _ = isolated_db
    assert session_module._factory is None

    session = session_module.SessionLocal()
    try:
        assert session_module._factory is not None
        assert session.get_bind() is connection_module.default_engine()
        assert session.execute(text("SELECT 1")).scalar() == 1
    finally:
        session.close()


def test_dispose_rebinds_engine_and_session_factory(isolated_db):
    connection_module, session_module, _ = isolated_db
    engine_first = connection_module.default_engine()
    first_session = session_module.SessionLocal()
    assert first_session.get_bind() is engine_first
    first_session.close()

    connection_module.dispose_default_engine()
    assert connection_module._default_engine is None
    assert session_module._factory is None

    engine_second = connection_module.default_engine()
    assert engine_second is not engine_first
    second_session = session_module.SessionLocal()
    try:
        assert second_session.get_bind() is engine_second
    finally:
        second_session.close()


def test_init_db_creates_tables_on_default_engine(isolated_db):
    connection_module, _, _ = isolated_db
    engine_obj = connection_module.init_db()
    assert engine_obj is connection_module.default_engine()
    table_names = set(inspect(engine_obj).get_table_names())
    assert {"task", "file", "user"} <= table_names
