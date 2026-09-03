"""
v0.51.1 BUG 修复回归测试

覆盖：
- 意图路由（强关键词优先，"报告PPT"不再误派给 Word）
- 分片上传绑定校验（metadata 无 upload_id 的记录必须被拒绝）
- 输出文件名唯一性（同秒并发不冲突）
- 软超时默认值
"""
import os
import asyncio
import io
import time
import types

import pytest


class TestRouteIntent:
    """route_intent 强关键词优先"""

    def test_report_with_ppt_keyword_routes_to_ppt(self):
        """“做一份季度总结报告PPT”含“报告”，但明确点名 PPT，应派给 PPT"""
        from office_agent.api.routing import route_intent
        agent, task_type, _ = route_intent("做一份季度总结报告PPT")
        assert agent == "ppt_agent"
        assert task_type == "ppt_generate"

    def test_ppt_with_layout_word_routes_to_ppt(self):
        """“排版这个PPT”含“排版”，但明确点名 PPT"""
        from office_agent.api.routing import route_intent
        agent, _, _ = route_intent("帮我排版这个PPT文件")
        assert agent == "ppt_agent"

    def test_weak_keyword_still_routes_to_word(self):
        """没有强关键词时，弱关键词保持旧行为"""
        from office_agent.api.routing import route_intent
        agent, _, _ = route_intent("帮我把报告排版成公文格式")
        assert agent == "word_agent"

    def test_excel_explicit(self):
        from office_agent.api.routing import route_intent
        agent, task_type, _ = route_intent("分析这个excel表格")
        assert agent == "excel_agent"
        assert task_type == "excel_analyze"

    def test_no_keyword_falls_back_to_orchestrator(self):
        from office_agent.api.routing import route_intent
        agent, task_type, _ = route_intent("你好")
        assert agent == "orchestrator"
        assert task_type == "general"

    def test_route_by_file_path(self):
        from office_agent.api.routing import route_by_file_path
        assert route_by_file_path("C:/tmp/a.pptx")[0] == "ppt_agent"
        assert route_by_file_path("C:/tmp/b.docx")[0] == "word_agent"
        assert route_by_file_path("C:/tmp/c.csv")[0] == "excel_agent"
        # pdf/txt/md 经 input_conversion 转换链由 word_agent 消费（清单 273 已关闭）
        assert route_by_file_path("C:/tmp/d.pdf")[0] == "word_agent"
        assert route_by_file_path("C:/tmp/e.bin") is None

    def test_agent_hint_is_normalized_and_validated(self):
        from pydantic import ValidationError
        from office_agent.api.schemas.request import ChatRequest

        assert ChatRequest(message="x", agent_hint="word_agent").agent_hint == "word"
        assert ChatRequest(message="x", agent_hint="workflow").agent_hint == "auto"
        with pytest.raises(ValidationError):
            ChatRequest(message="x", agent_hint="unknown")

    def test_explicit_agent_switch_starts_new_revision_chain(self):
        from office_agent.api.router.chat import _is_explicit_cross_agent

        assert _is_explicit_cross_agent("ppt", "ppt_agent", "word_agent") is True
        assert _is_explicit_cross_agent("word", "word_agent", "word_agent") is False
        assert _is_explicit_cross_agent("auto", "ppt_agent", "word_agent") is False


class TestMultipartBinding:
    """分片上传绑定校验"""

    def _db_file_stub(self, metadata_json: str):
        return types.SimpleNamespace(metadata_json=metadata_json)

    def test_unbound_metadata_rejected(self):
        """普通上传（metadata 无 upload_id）不允许提交分片"""
        from office_agent.storage.storage_service import StorageService
        with pytest.raises(ValueError):
            StorageService._verify_upload_binding(
                self._db_file_stub('{"content_type": "text/csv"}'), "mp_abc")

    def test_wrong_upload_id_rejected(self):
        from office_agent.storage.storage_service import StorageService
        with pytest.raises(ValueError):
            StorageService._verify_upload_binding(
                self._db_file_stub('{"upload_id": "mp_right"}'), "mp_wrong")

    def test_matching_upload_id_accepted(self):
        from office_agent.storage.storage_service import StorageService
        StorageService._verify_upload_binding(
            self._db_file_stub('{"upload_id": "mp_ok"}'), "mp_ok")

    def test_broken_metadata_rejected(self):
        from office_agent.storage.storage_service import StorageService
        with pytest.raises(ValueError):
            StorageService._verify_upload_binding(
                self._db_file_stub('not-json'), "mp_ok")

    def test_part_number_must_start_at_one(self):
        from office_agent.storage.storage_service import StorageService
        service = StorageService.__new__(StorageService)
        with pytest.raises(ValueError, match="part_number"):
            service.upload_part("file_x", "mp_x", 0, b"data")

    def test_local_multipart_rejects_wrong_etag(self, tmp_path):
        from office_agent.storage.local_storage import LocalStorage
        backend = LocalStorage(str(tmp_path))
        upload_id = backend.init_multipart_upload("uploads/test.bin")
        backend.upload_part("uploads/test.bin", upload_id, 1, b"correct")
        with pytest.raises(ValueError, match="校验失败"):
            backend.complete_multipart_upload(
                "uploads/test.bin", upload_id,
                [{"part_number": 1, "etag": "wrong"}],
            )

    def test_streamed_upload_stops_at_limit(self):
        from fastapi import HTTPException, UploadFile
        from office_agent.api.router.file import _read_upload_limited

        upload = UploadFile(filename="large.bin", file=io.BytesIO(b"12345"))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(_read_upload_limited(upload, 4))
        assert exc.value.status_code == 413


class TestOutputNaming:
    """输出文件名同秒唯一"""

    def test_safe_output_unique_within_same_second(self):
        from office_agent.task_queue.tasks.word_tasks import _safe_output
        a = _safe_output()
        b = _safe_output()
        assert a != b
        assert os.path.basename(a).startswith("word_")

    def test_excel_safe_output_unique_within_same_second(self):
        from office_agent.task_queue.tasks.excel_tasks import _safe_output
        assert _safe_output() != _safe_output()

    def test_ppt_safe_filename_unique_within_same_second(self):
        from office_agent.task_queue.tasks.ppt_tasks import _safe_filename
        assert _safe_filename("测试") != _safe_filename("测试")


class TestWorkerConfig:
    """软超时默认值应覆盖慢任务（LLM 规划 + 多张生图）"""

    def test_default_soft_timeout_covers_slow_tasks(self):
        from office_agent.task_queue.config import config
        assert config.TASK_SOFT_TIMEOUT >= 1800

    def test_storage_service_exports_stream_download_annotation_types(self):
        """stream_download 的 Iterator 注解必须可求值（Py3.10-3.13 兼容）"""
        import typing
        from office_agent.storage import storage_service as mod
        hints = typing.get_type_hints(mod.StorageService.stream_download)
        assert hints["return"] is not None

    def test_fast_task_future_is_cleaned(self):
        """极快任务也不能在 Future 注册竞态中永久滞留为 active。"""
        from office_agent.task_queue.worker import LocalWorker

        worker = LocalWorker()
        worker._session_factory = None
        worker.register("test.quick", lambda **_kwargs: {"status": "success"})
        try:
            worker.submit("test.quick", task_id="task_fast_cleanup")
            deadline = time.time() + 3
            while time.time() < deadline and worker.get_active_count():
                time.sleep(0.01)
            assert worker.get_active_count() == 0
            assert "task_fast_cleanup" not in worker._futures
        finally:
            worker.shutdown()


class TestModelManagerIsolation:
    """环境变量模型配置不能污染进程级默认模板。"""

    def test_env_key_does_not_leak_to_later_manager(self, tmp_path, monkeypatch):
        from office_agent.model_gateway.model_manager import ModelManager
        from office_agent.models.model_schemas import DEFAULT_MODEL_CONFIGS, ModelProvider

        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-key")
        configured = ModelManager(str(tmp_path / "configured"))
        assert configured.get_model("deepseek-default").api_key == "test-only-key"

        monkeypatch.delenv("DEEPSEEK_API_KEY")
        clean = ModelManager(str(tmp_path / "clean"))
        assert clean.get_model("deepseek-default") is None
        assert DEFAULT_MODEL_CONFIGS[ModelProvider.DEEPSEEK].api_key == ""

    def test_model_test_errors_are_user_safe(self):
        from office_agent.api.router.settings import _model_test_error

        assert _model_test_error("HTTP 401 key rejected") == "鉴权失败，请更新 API Key"
        assert _model_test_error("HTTP 429 rate limit") == "请求过于频繁或额度不足，请稍后重试"
        assert "sk-secret" not in _model_test_error("unexpected sk-secret")

    def test_image_test_errors_are_actionable_and_safe(self):
        from office_agent.api.router.settings import _image_test_error

        assert "API Key" in _image_test_error("HTTP 401 sk-secret")
        assert "Base URL" in _image_test_error("getaddrinfo failed")
        assert "sk-secret" not in _image_test_error("unexpected sk-secret")

    def test_ppt_image_failure_summary(self):
        from office_agent.task_queue.tasks.ppt_tasks import _image_failure_summary

        assert _image_failure_summary("HTTP 429 rate limit")[0] == "quota"
        assert _image_failure_summary("getaddrinfo failed")[0] == "connection"

    def test_legacy_model_config_is_not_copied_across_different_salt(self, tmp_path, monkeypatch):
        from pathlib import Path
        from office_agent.model_gateway.model_manager import HAS_CRYPTO, resolve_model_config_dir

        if not HAS_CRYPTO:
            pytest.skip("Fernet 不可用时旧 XOR 配置不依赖 salt")

        fake_home = tmp_path / "home"
        legacy = fake_home / ".office_agent"
        target = tmp_path / "appdata"
        legacy.mkdir(parents=True)
        target.mkdir()
        (legacy / "key_salt.bin").write_bytes(b"legacy-salt")
        (legacy / "models.json").write_text('{"models": []}', encoding="utf-8")
        (target / "key_salt.bin").write_bytes(b"different-salt")

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(target))

        assert resolve_model_config_dir() == target
        assert not (target / "models.json").exists()
