"""Regression coverage for the post-incident follow-up fixes."""
from __future__ import annotations

import io
import threading
import zipfile

import pytest


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


def test_storage_upload_validation_invokes_authoritative_scanner(monkeypatch):
    from office_agent.security.file_security.file_scanner import (
        FileScanner,
        ScanResult,
        ThreatLevel,
    )
    from office_agent.storage.validators import FileValidationError, validate_file

    calls = []

    def block(self, fileobj, filename):
        calls.append((filename, fileobj.tell()))
        return ScanResult(
            filename=filename,
            file_size=4,
            threat_level=ThreatLevel.BLOCKED,
            is_allowed=False,
            detected_threats=["scanner-blocked"],
            extension=".txt",
        )

    monkeypatch.setattr(FileScanner, "scan_fileobj", block)
    with pytest.raises(FileValidationError, match="scanner-blocked"):
        validate_file("notes.txt", b"safe-looking text")
    assert calls == [("notes.txt", 0)]


def test_storage_upload_blocks_archive_traversal_and_zip_bomb():
    from office_agent.storage.validators import FileValidationError, validate_file

    traversal = _zip_bytes({
        "[Content_Types].xml": b"<Types/>",
        "_rels/.rels": b"<Relationships/>",
        "word/document.xml": b"<document/>",
        "../outside.txt": b"x",
    })
    with pytest.raises(FileValidationError, match="路径穿越"):
        validate_file("unsafe.docx", traversal)

    compressed = _zip_bytes({
        "[Content_Types].xml": b"<Types/>",
        "_rels/.rels": b"<Relationships/>",
        "word/document.xml": b"0" * (1024 * 1024),
    })
    with pytest.raises(FileValidationError, match="压缩比"):
        validate_file("bomb.docx", compressed)


def test_jwt_revocation_file_is_parsed_only_when_metadata_changes(tmp_path, monkeypatch):
    from office_agent.security.auth import JWTManager

    manager = JWTManager(state_dir=tmp_path)
    token = manager.create_access_token("user-1", "alice")
    calls = 0
    original = manager._load_revocations

    def counted_load():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(manager, "_load_revocations", counted_load)
    assert manager.decode(token).user_id == "user-1"
    assert manager.decode(token).user_id == "user-1"
    assert calls == 0

    other = JWTManager(state_dir=tmp_path)
    assert other.revoke(token)
    assert manager.verify(token) is None
    assert calls == 1


def test_refresh_tokens_are_rotated_and_cannot_be_replayed(tmp_path):
    from office_agent.security.auth import JWTManager, TokenManager

    jwt = JWTManager(state_dir=tmp_path)
    manager = TokenManager(jwt_manager=jwt, session_factory=lambda: None)
    original = manager.login("user-1", "alice")["refresh_token"]

    rotated = manager.refresh(original)

    assert jwt.decode(rotated["access_token"]).user_id == "user-1"
    assert jwt.decode(rotated["refresh_token"], expected_type="refresh").user_id == "user-1"
    with pytest.raises(ValueError, match="撤销"):
        jwt.decode(original, expected_type="refresh")
    with pytest.raises(ValueError, match="撤销"):
        manager.refresh(original)


def test_version_lock_entries_are_reference_counted_and_reclaimed():
    from office_agent.storage.storage_service import StorageService

    service = object.__new__(StorageService)
    service._version_locks = {}
    service._version_locks_guard = threading.Lock()

    entered = threading.Event()
    release = threading.Event()

    def holder():
        with service._version_lock("file-1"):
            entered.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(timeout=5)
    assert service._version_locks["file-1"][1] == 1
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert service._version_locks == {}


def test_reset_db_requires_explicit_confirmation(monkeypatch):
    import manage
    import office_agent.database.connection as connection

    calls = []
    monkeypatch.setattr(connection, "init_db", lambda drop_all=False: calls.append(drop_all))
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    manage.reset_db()
    assert calls == []


def test_vision_render_state_is_restored_and_truncation_is_reported():
    from types import SimpleNamespace

    from office_agent.vision_gateway.gateway import VisionGateway
    from office_agent.vision_gateway.vision_models import (
        DocumentPage,
        ImageInput,
        VisionResponse,
    )

    gateway = object.__new__(VisionGateway)
    observed = []

    def render(_path):
        observed.append((gateway.renderer.dpi, gateway.renderer.max_pages))
        return [DocumentPage(page_number=1, image=ImageInput.from_buffer(b"image"))]

    gateway.renderer = SimpleNamespace(
        dpi=150,
        max_pages=30,
        last_total_pages=42,
        render=render,
    )
    gateway.analyze = lambda _request, _model_key: VisionResponse(
        success=True, content="ok"
    )

    result = gateway.analyze_document("report.pdf", dpi=96, max_pages=5)

    assert observed == [(96, 5)]
    assert (gateway.renderer.dpi, gateway.renderer.max_pages) == (150, 30)
    assert result.page_count == 1
    assert result.source_page_count == 42
    assert result.truncated is True


def test_rag_source_does_not_persist_local_absolute_path(tmp_path, monkeypatch):
    from office_agent.task_queue.tasks import rag_tasks

    document = tmp_path / "private" / "report.txt"
    document.parent.mkdir()
    document.write_text("content", encoding="utf-8")
    observed = []

    monkeypatch.setattr(
        rag_tasks,
        "_chunks_from_file",
        lambda *_args: ("Report", [object()]),
    )
    monkeypatch.setattr(
        rag_tasks,
        "_store_chunks",
        lambda _chunks, _title, _category, source: observed.append(source) or 1,
    )

    result = rag_tasks.index_document(str(document))

    assert result["status"] == "success"
    assert observed == ["report.txt"]
    assert rag_tasks._public_source("https://example.com/report", str(document)) == (
        "https://example.com/report"
    )
