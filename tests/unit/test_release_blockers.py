"""Regression coverage for release-blocking findings in CODE_REVIEW_REPORT.md."""
import hashlib

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _storage_service(tmp_path, request):
    from office_agent.database.base import Base
    import office_agent.database.models  # noqa: F401
    from office_agent.storage.local_storage import LocalStorage
    from office_agent.storage.storage_service import StorageConfig, StorageService

    engine = create_engine(f"sqlite:///{tmp_path / 'storage.db'}")
    request.addfinalizer(engine.dispose)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = StorageService(
        StorageConfig(local_path=str(tmp_path / "objects")),
        LocalStorage(str(tmp_path / "objects")),
    )
    service._get_session = factory
    return service, factory


def test_spoofed_office_file_and_legacy_formats_are_rejected():
    from office_agent.storage.validators import FileValidationError, validate_file

    with pytest.raises(FileValidationError, match="不支持"):
        validate_file("legacy.doc", b"legacy")
    with pytest.raises(FileValidationError, match="不匹配"):
        validate_file("renamed.docx", b"MZ executable")


def test_valid_ooxml_upload_is_streamed_and_persisted(tmp_path, sample_docx, request):
    service, _ = _storage_service(tmp_path, request)
    with sample_docx.open("rb") as stream:
        info = service.upload_fileobj("report.docx", stream)
    assert info.file_hash == hashlib.sha256(sample_docx.read_bytes()).hexdigest()
    assert service.download(info.file_id)[0][:2] == b"PK"


def test_multipart_requires_every_expected_part_and_whole_hash(tmp_path, request):
    service, _ = _storage_service(tmp_path, request)
    payload = b"AAAABBBB"
    init = service.init_multipart_upload(
        "notes.txt",
        expected_size=len(payload),
        expected_parts=2,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )
    first = service.upload_part(init["file_id"], init["upload_id"], 1, b"AAAA")
    second = service.upload_part(init["file_id"], init["upload_id"], 2, b"BBBB")

    with pytest.raises(ValueError, match="不能缺片"):
        service.complete_multipart_upload(
            init["file_id"], init["upload_id"], [second]
        )

    completed = service.complete_multipart_upload(
        init["file_id"], init["upload_id"], [first, second]
    )
    assert completed.status == "ready"
    assert service.download(completed.file_id)[0] == payload


def test_version_paths_are_all_referenced_and_permanent_delete_cleans_them(tmp_path, request):
    service, factory = _storage_service(tmp_path, request)
    initial = service.upload("notes.txt", b"first version content")
    old_path = initial.storage_path
    second = service.create_version(initial.file_id, b"second version content")
    assert service.get_versions(initial.file_id)[0]["storage_path"] == old_path
    assert service.backend.exists(old_path)
    assert service.backend.exists(second.storage_path)

    restored = service.restore_version(initial.file_id, 1)
    referenced = {restored.storage_path}
    referenced.update(v["storage_path"] for v in service.get_versions(initial.file_id))
    physical = {item["path"].replace("/", "\\")
                for item in service.backend.list_files()}
    assert physical == {path.replace("/", "\\") for path in referenced}

    assert service.delete(initial.file_id, permanent=True) is True
    assert service.backend.list_files() == []
    session = factory()
    try:
        from office_agent.database.models import File, FileVersion
        assert session.query(File).count() == 0
        assert session.query(FileVersion).count() == 0
    finally:
        session.close()


def test_recycle_bin_lists_restores_and_permanently_deletes_files(tmp_path, request):
    service, _ = _storage_service(tmp_path, request)
    info = service.upload("meeting-notes.txt", b"approved minutes")

    assert service.delete(info.file_id) is True
    assert service.list_files() == []
    deleted = service.list_deleted_files()
    assert [item.file_id for item in deleted] == [info.file_id]
    assert deleted[0].deleted_at is not None
    assert service.count_deleted_files() == 1

    restored = service.restore_deleted(info.file_id)
    assert restored.status == "ready"
    assert service.download(info.file_id)[0] == b"approved minutes"
    assert service.count_deleted_files() == 0

    assert service.delete(info.file_id) is True
    assert service.delete(info.file_id, permanent=True) is True
    assert service.list_deleted_files() == []
    assert service.backend.list_files() == []


def test_recycle_bin_refuses_to_restore_missing_physical_content(tmp_path, request):
    service, _ = _storage_service(tmp_path, request)
    info = service.upload("missing.txt", b"content")
    assert service.delete(info.file_id) is True
    service.backend.delete(info.storage_path)

    with pytest.raises(RuntimeError, match="内容已缺失"):
        service.restore_deleted(info.file_id)
    assert service.count_deleted_files() == 1


def test_recycle_bin_api_contract_is_not_shadowed_by_file_id_route(monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from office_agent.api.router import file as file_router

    deleted = SimpleNamespace(
        file_id="file_deleted", original_name="board.docx", file_type="word",
        extension=".docx", size=2048, version=1, status="deleted",
        created_at=datetime(2026, 8, 30), deleted_at=datetime(2026, 8, 31),
    )

    class FakeStorage:
        def list_deleted_files(self, **_kwargs):
            return [deleted]

        def count_deleted_files(self, **_kwargs):
            return 1

        def restore_deleted(self, file_id):
            assert file_id == "file_deleted"
            return SimpleNamespace(**{
                **deleted.__dict__, "status": "ready", "deleted_at": None,
            })

    monkeypatch.setattr(file_router, "_get_storage", lambda: FakeStorage())
    app = FastAPI()
    app.include_router(file_router.router)
    client = TestClient(app)

    listing = client.get("/api/file/trash")
    assert listing.status_code == 200
    assert listing.json()["data"]["files"][0]["deleted_at"].startswith("2026-08-31")

    restored = client.post("/api/file/file_deleted/restore")
    assert restored.status_code == 200
    assert restored.json()["data"]["status"] == "ready"


def test_registered_agent_output_removes_temporary_copy(tmp_path, sample_docx, request):
    service, _ = _storage_service(tmp_path, request)
    generated = tmp_path / "generated.docx"
    generated.write_bytes(sample_docx.read_bytes())
    info = service.save_new_output(str(generated), "result.docx")
    assert info.file_id
    assert not generated.exists()
    assert service.backend.exists(info.storage_path)


def test_request_models_reject_multiple_primary_files():
    from office_agent.api.schemas.request import ChatRequest, TaskCreateRequest

    with pytest.raises(ValidationError, match="只支持一个主文件"):
        ChatRequest(message="处理", file_ids=["a", "b"])
    with pytest.raises(ValidationError, match="只支持一个主文件"):
        TaskCreateRequest(task_type="word_format", instruction="处理",
                          file_ids=["a", "b"])


def test_hashing_embedder_is_restart_stable():
    from office_agent.knowledge_base.embeddings import HashingEmbedder

    first = HashingEmbedder().embed_query("季度合规 policy")
    second = HashingEmbedder().embed_query("季度合规 policy")
    assert first == second
    assert len(first) == 512


def test_registered_rag_tasks_work_against_real_database(
        tmp_path, monkeypatch, request, fake_semantic_embedder):
    from office_agent.database.base import Base
    import office_agent.database.models  # noqa: F401
    from office_agent.database import session as session_module
    from office_agent.task_queue.tasks.rag_tasks import (
        chunk_and_embed, refresh_knowledge_base, search_knowledge,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'rag.db'}")
    request.addfinalizer(engine.dispose)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        session_module, "SessionLocal",
        sessionmaker(bind=engine, expire_on_commit=False),
    )

    embedded = chunk_and_embed(
        "季度归档规则要求所有报告保留七年并进行合规复核。",
        title="归档规则",
        category="policy",
    )
    assert embedded["status"] == "success"
    assert embedded["chunks"] >= 1

    found = search_knowledge("归档", category="policy")
    assert found["status"] == "success"
    assert found["results"][0]["title"] == "归档规则"
    assert found["results"][0]["score"] > 0

    refreshed = refresh_knowledge_base()
    assert refreshed == {"status": "success", "refreshed": 1, "total": 1}


@pytest.mark.parametrize(
    ("module_name", "function_name", "kwargs"),
    [
        ("office_agent.task_queue.tasks.word_tasks", "process_word",
         {"input_path": "missing.docx"}),
        ("office_agent.task_queue.tasks.excel_tasks", "analyze_excel",
         {"input_path": "missing.xlsx"}),
    ],
)
def test_office_failure_paths_preserve_sanitized_original_error(
        module_name, function_name, kwargs):
    module = __import__(module_name, fromlist=[function_name])
    result = getattr(module, function_name)(**kwargs)
    assert result["status"] == "failed"
    assert "UnboundLocalError" not in result["error"]
    assert "文件不存在" in result["error"]
