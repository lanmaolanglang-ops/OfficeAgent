"""RC 回归：首轮对话由服务端生成的 conversation_id 必须落库到任务行，
否则前端用首轮响应里的 cid 发起 follow-up 时无法关联（is_follow_up 永远为 False）。"""
import pytest


@pytest.fixture
def isolated_chat_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from office_agent.database.base import Base
    import office_agent.database.models  # noqa: F401  注册所有表
    from office_agent.database import session as session_module
    from office_agent.api.main import create_app
    import office_agent.task_queue as tq

    engine = create_engine(
        f"sqlite:///{tmp_path / 'chat.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "SessionLocal", maker)
    # 不需要真正跑 worker：任务行在入队前已创建
    monkeypatch.setattr(tq, "init_worker", lambda *a, **k: None)
    monkeypatch.setattr(tq, "submit_task", lambda *a, **k: None)

    app = create_app()
    client = TestClient(app)  # 不进入 with：不触发 lifespan/真实迁移
    client._rc_maker = maker
    yield client
    engine.dispose()


HOST = {"Host": "127.0.0.1:8765"}


def test_first_turn_minted_cid_is_persisted(isolated_chat_client):
    client = isolated_chat_client
    r1 = client.post("/api/chat", json={"message": "帮我写一份项目文档", "agent_hint": "word"},
                     headers=HOST)
    assert r1.status_code == 200, r1.text
    cid = r1.json()["data"]["conversation_id"]
    assert cid  # 服务端必须返回 cid

    from office_agent.database.models.task import Task
    session = client._rc_maker()
    try:
        tasks = session.query(Task).all()
        assert len(tasks) == 1
        # 关键：首轮任务行必须带上同一个 cid（修复前为 NULL）
        assert tasks[0].conversation_id == cid
    finally:
        session.close()


def test_client_supplied_cid_is_persisted(isolated_chat_client):
    client = isolated_chat_client
    r = client.post("/api/chat",
                    json={"message": "做表", "agent_hint": "excel",
                          "conversation_id": "conv-fixed-1"},
                    headers=HOST)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["conversation_id"] == "conv-fixed-1"
    from office_agent.database.models.task import Task
    session = client._rc_maker()
    try:
        assert session.query(Task).one().conversation_id == "conv-fixed-1"
    finally:
        session.close()
