"""SQLAlchemy DML ``rowcount`` 适配器回归测试（Mypy Phase 4 Cluster 3）。

根因：SQLAlchemy 2.0 ``Session.execute`` 静态返回类型恒为 ``Result[Any]``，
而运行时 DML（update/delete）实际返回 ``CursorResult``（才有 ``rowcount``）。
修复：``BaseRepository._execute_rowcount`` 把该运行时不变式显式化，
各 DML 调用点（task/template/skill/knowledge 仓库、audit 清理、日志清理）
不再直接 ``session.execute(...).rowcount``。

本文件验证的是**运行时契约**：``_execute_rowcount`` 返回受影响行数，
与旧 ``session.execute(...).rowcount`` 语义完全一致（cast 只作用于类型层）。
"""
import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from office_agent.database.base import Base
from office_agent.database.models.task import Task
from office_agent.database.repository.task_repo import TaskRepository


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_execute_rowcount_returns_affected_rows_for_update(db_session):
    """start_task 依赖 _execute_rowcount：命中返回 True，未命中返回 False。"""
    repo = TaskRepository(db_session)
    task = repo.create_task("word_format", "do something")
    db_session.commit()

    # pending → running：命中 1 行
    assert repo.start_task(task.id) is True
    # 已是 running，状态过滤命中 0 行
    assert repo.start_task(task.id) is False
    # 不存在的 id：命中 0 行
    assert repo.start_task("nonexistent") is False


def test_execute_rowcount_delete_returns_affected_rows(db_session):
    """_execute_rowcount 直接用于 delete：返回精确受影响行数。"""
    repo = TaskRepository(db_session)
    a = repo.create_task("word_format", "first")
    b = repo.create_task("ppt_generate", "second")
    db_session.commit()

    assert repo._execute_rowcount(delete(Task).where(Task.id == a.id)) == 1
    assert repo._execute_rowcount(delete(Task).where(Task.id == a.id)) == 0
    # 保留另一行未被误删
    assert db_session.get(Task, b.id) is not None


def test_increment_usage_style_repos_return_int(db_session):
    """template/skill/knowledge 仓库的 increment_usage 返回 int 行数。"""
    from office_agent.database.repository.skill_repo import SkillRepository
    from office_agent.database.models.skill import Skill

    repo = SkillRepository(db_session)
    skill = repo.create_skill("summarize", prompt="summarize text")
    db_session.commit()

    rows = repo.increment_usage(skill.id)
    assert isinstance(rows, int)
    assert rows == 1
    # usage_count 确实 +1
    db_session.expire_all()
    assert db_session.get(Skill, skill.id).usage_count == 1
