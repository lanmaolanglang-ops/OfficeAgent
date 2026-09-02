"""Regression tests for the low-risk follow-up review fixes."""
import asyncio
import json
from types import SimpleNamespace


def test_all_vision_clients_reject_empty_model_content(monkeypatch):
    from office_agent.vision_gateway.clients.claude_client import ClaudeVisionClient
    from office_agent.vision_gateway.clients.gemini_client import GeminiVisionClient
    from office_agent.vision_gateway.clients.openai_client import OpenAIVisionClient
    from office_agent.vision_gateway.vision_models import ImageInput, VisionRequest

    request = VisionRequest(
        images=[ImageInput.from_buffer(b"image")],
        require_structured=False,
    )
    cases = [
        (OpenAIVisionClient("key"), {"choices": [{"message": {"content": "  "}}]}),
        (ClaudeVisionClient("key"), {"content": []}),
        (
            GeminiVisionClient("key"),
            {"candidates": [{"content": {"parts": []}}]},
        ),
    ]
    for client, response in cases:
        monkeypatch.setattr(client, "_http_post", lambda *_args, _r=response: _r)
        result = client.analyze(request)
        assert result.success is False
        assert "空内容" in result.error


def test_model_router_infers_from_message_lists():
    from office_agent.model_gateway.model_router import ModelRouter
    from office_agent.models.model_schemas import AITaskType, ChatMessage

    router = object.__new__(ModelRouter)
    assert router.infer_task_type([{"role": "user", "content": "请分析截图"}]) == AITaskType.VISION
    assert router.infer_task_type([ChatMessage(role="user", content="生成 Python 代码")]) == AITaskType.CODE_GENERATION


def test_sqlite_engine_enables_foreign_keys():
    from sqlalchemy import text
    from office_agent.database.connection import get_engine

    engine = get_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    engine.dispose()


def test_explicit_non_sqlite_engine_drops_sqlite_connect_args(monkeypatch):
    from office_agent.database import connection as connection_module

    captured = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr(connection_module, "create_engine", fake_create_engine)
    connection_module.get_engine("postgresql://example/test")
    assert captured["url"].startswith("postgresql://")
    assert "connect_args" not in captured["kwargs"]
    assert captured["kwargs"]["pool_size"] > 0


def test_error_log_none_values_are_safe(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from office_agent.database.base import Base
    from office_agent.database.models.execution import ErrorLog
    from office_agent.database.repository.execution_repo import ErrorLogRepository

    assert repr(ErrorLog(error_type="Example", error_message=None)) == "<ErrorLog Example: >"

    engine = create_engine(f"sqlite:///{tmp_path / 'errors.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        created = ErrorLogRepository(session).log_error(None, None)
        session.commit()
        assert created.error_type == "UnknownError"


def test_cleanup_endpoint_tolerates_missing_cleaned_count(monkeypatch):
    from office_agent.api.router import file as file_router

    monkeypatch.setattr(
        file_router,
        "_get_storage",
        lambda: SimpleNamespace(cleanup_temp_files=lambda _hours: {"skipped": 2}),
    )
    response = asyncio.run(file_router.cleanup_temp(24))
    assert response.data == {"skipped": 2}
    assert response.message == "清理了 0 个文件"


def test_ppt_ai_outline_accepts_uppercase_json_fence():
    from office_agent.ppt_agent.content_planner import ContentPlanner

    payload = {
        "slides": [
            {"layout": "cover", "title": "真实主题"},
            {"layout": "summary", "title": "总结", "bullets": ["结论"]},
        ]
    }
    gateway = SimpleNamespace(
        chat=lambda **_kwargs: SimpleNamespace(
            success=True,
            content=f"```JSON\n{json.dumps(payload, ensure_ascii=False)}\n```",
            error="",
        )
    )
    outline = ContentPlanner(model_gateway=gateway).plan_from_theme(
        "真实主题", slide_count=2
    )
    assert [slide.title for slide in outline.slides] == ["真实主题", "总结"]
