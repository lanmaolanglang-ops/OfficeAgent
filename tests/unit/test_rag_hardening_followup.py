from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _configure_database(tmp_path, monkeypatch, request):
    from office_agent.database.base import Base
    import office_agent.database.models  # noqa: F401
    from office_agent.database import session as session_module

    engine = create_engine(f"sqlite:///{tmp_path / 'rag-hardening.db'}")
    request.addfinalizer(engine.dispose)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    return factory


def test_repeated_manual_index_is_idempotent(tmp_path, monkeypatch, request):
    from office_agent.database.repository import KnowledgeRepository
    from office_agent.task_queue.tasks.rag_tasks import chunk_and_embed

    factory = _configure_database(tmp_path, monkeypatch, request)
    text = "季度归档规则要求所有报告保留七年并进行合规复核。"

    first = chunk_and_embed(text, title="归档规则", category="policy")
    second = chunk_and_embed(text, title="归档规则", category="policy")

    assert first["status"] == second["status"] == "success"
    with factory() as session:
        assert KnowledgeRepository(session).count() == first["chunks"]


def test_category_search_pages_and_reports_candidate_truncation(
        tmp_path, monkeypatch, request):
    from office_agent.database.repository import KnowledgeRepository
    from office_agent.task_queue.tasks.rag_tasks import search_knowledge

    factory = _configure_database(tmp_path, monkeypatch, request)
    with factory() as session:
        repo = KnowledgeRepository(session)
        for index in range(105):
            repo.add_knowledge(
                title=f"规则 {index}",
                content="普通条目" if index < 104 else "唯一命中词：星海归档",
                category="policy",
            )
        session.commit()

    complete = search_knowledge("星海归档", category="policy", max_candidates=200)
    limited = search_knowledge("普通条目", category="policy", max_candidates=100)

    assert complete["results"][0]["title"] == "规则 104"
    assert complete["candidate_count"] == 105
    assert complete["truncated"] is False
    assert limited["candidate_count"] == 100
    assert limited["candidate_limit"] == 100
    assert limited["truncated"] is True
