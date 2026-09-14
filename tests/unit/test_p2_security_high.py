"""Batch 2 安全 HIGH 回归测试。"""
from __future__ import annotations

import pytest


class TestPublicProbePaths:
    def test_live_ready_are_public(self):
        from office_agent.api.middleware.auth import AuthMiddleware

        assert "/live" in AuthMiddleware.PUBLIC_PATHS
        assert "/ready" in AuthMiddleware.PUBLIC_PATHS
        assert "/api/health" in AuthMiddleware.PUBLIC_PATHS


class TestMultipartOwnership:
    def test_init_is_not_treated_as_file_id(self):
        from office_agent.api.middleware.auth import AuthMiddleware

        assert AuthMiddleware._resource_target(
            "/api/file/upload/multipart/init"
        ) is None
        assert AuthMiddleware._resource_target(
            "/api/file/upload/multipart/complete"
        ) is None
        # 真实 file_id 仍做 ownership
        assert AuthMiddleware._resource_target(
            "/api/file/upload/multipart/file_abc"
        ) == ("file", "file_abc")


class TestSanitizeErrorSecrets:
    def test_hides_bearer_and_jwt(self):
        from office_agent.security.error_sanitizer import sanitize_error

        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        text = sanitize_error(RuntimeError(f"Authorization: Bearer {jwt}"))
        assert jwt not in text
        assert "Bearer [已隐藏]" in text or "[已隐藏]" in text

    def test_hides_windows_and_unc_paths(self):
        from office_agent.security.error_sanitizer import sanitize_error

        text = sanitize_error(RuntimeError(r"failed D:\secret\key.pem and \\\\server\\share\\x"))
        assert r"D:\secret" not in text
        assert "路径已隐藏" in text

    def test_keeps_actionable_message(self):
        from office_agent.security.error_sanitizer import sanitize_error

        assert "文件不存在" in sanitize_error(RuntimeError("文件不存在: report.docx"))


class TestTaskListOwnerFilter:
    def test_memory_fallback_uses_filtered_snapshots(self, monkeypatch):
        """DB 降级路径不得回退到未过滤的 list_tasks。"""
        class FakeTM:
            def snapshot_tasks(self, **kwargs):
                return []

            def list_tasks(self, **kwargs):
                raise AssertionError("fallback must not call unfiltered list_tasks")

        monkeypatch.setattr(
            "office_agent.api.router.task.task_manager", FakeTM()
        )
        monkeypatch.setattr(
            "office_agent.api.router.task._get_db_session", lambda: None
        )
        import asyncio

        from office_agent.api.router.task import _list_tasks_impl

        result = asyncio.run(_list_tasks_impl(page=1, page_size=20))
        assert result is not None


class TestFileSecurityUserId:
    def test_rejects_path_traversal_user_id(self, tmp_path):
        from office_agent.security.file_security import FileSecurityManager

        mgr = FileSecurityManager(storage_root=tmp_path)
        for bad in ("../evil", "..\\evil", "a/b", "a\\b", "", ".", "..", "x" * 100):
            with pytest.raises(ValueError):
                mgr.get_user_space(bad)

    def test_accepts_normal_user_id(self, tmp_path):
        from office_agent.security.file_security import FileSecurityManager

        mgr = FileSecurityManager(storage_root=tmp_path)
        space = mgr.get_user_space("user_001")
        assert space.root.name == "user_001"


class TestXxeIsDangerous:
    def test_xxe_pattern_blocks(self, tmp_path):
        from office_agent.security.file_security import FileScanner

        evil = tmp_path / "evil.txt"
        evil.write_text(
            '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><f>&xxe;</f>',
            encoding="utf-8",
        )
        result = FileScanner().scan_file(str(evil), "evil.txt")
        assert not result.is_allowed
        assert result.threat_level.value == "dangerous"


class TestImageBaseUrlSsrf:
    def test_rejects_private_base_url(self):
        from office_agent.image_generation.gateway import (
            ImageGenerationError,
            ImageGenerationGateway,
        )

        gw = ImageGenerationGateway(
            api_key="sk-test",
            base_url="http://127.0.0.1:9",
            provider="agnes",
        )
        with pytest.raises(ImageGenerationError):
            gw.generate("prompt")

    def test_rejects_localhost_mcp(self):
        from office_agent.image_generation.gateway import (
            ImageGenerationError,
            ImageGenerationGateway,
        )

        gw = ImageGenerationGateway(
            provider="mcp",
            mcp_url="http://localhost:8080",
            api_key="",
        )
        with pytest.raises(ImageGenerationError):
            gw.generate("prompt")


class TestLogInputSecretRedaction:
    def test_sensitive_kwargs_hidden(self):
        from office_agent.logging_system.decorators import _summarize_input

        def sample(api_key="x", token="y", filename="a.docx"):
            pass

        summary = _summarize_input(
            sample, (), {"api_key": "sk-secret", "token": "tok", "filename": "a.docx"},
            True, 500,
        )
        assert "sk-secret" not in summary
        assert "tok" not in summary or "token=[已隐藏]" in summary
        assert "a.docx" in summary


class TestCancelLogsQueueFailure:
    def test_bare_except_removed(self):
        from pathlib import Path

        src = Path(__file__).resolve().parents[2] / (
            "office_agent/api/router/task.py"
        )
        text = src.read_text(encoding="utf-8")
        # 修复后不应再有 cancel 路径的裸 except pass
        assert "except Exception:\n        pass" not in text


class TestToolRegistryIsolation:
    def test_mutating_copy_does_not_touch_default(self):
        from office_agent.security.permission.agent_permissions import (
            DEFAULT_TOOLS,
            ToolRegistry,
        )

        before = DEFAULT_TOOLS["docx_parser"].requires_approval
        reg = ToolRegistry()
        tool = reg.get("docx_parser")
        tool.requires_approval = not before
        tool.allowed_agents.add("evil-agent")
        assert DEFAULT_TOOLS["docx_parser"].requires_approval is before
        assert "evil-agent" not in DEFAULT_TOOLS["docx_parser"].allowed_agents


class TestPromptStrictModeWiring:
    def test_scanner_honors_config_strict_mode(self, monkeypatch):
        from office_agent.security.config import SecurityConfig, set_security_config

        cfg = SecurityConfig()
        cfg.prompt_strict_mode = True
        set_security_config(cfg)
        from office_agent.api.core import prompt_policy

        monkeypatch.setattr(
            prompt_policy, "_prompt_scanner", prompt_policy._build_scanner()
        )
        assert prompt_policy._prompt_scanner.strict_mode is True
        set_security_config(SecurityConfig())
