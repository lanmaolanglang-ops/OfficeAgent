"""Targeted regressions for the remaining deep-review fixes."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest


class TestFileMetadata:
    def test_invalid_root_isolated_and_atomic_round_trip(self, temp_dir):
        from office_agent.api.core.file_manager import FileInfo, FileManager

        upload = temp_dir / "uploads"
        output = temp_dir / "outputs"
        upload.mkdir()
        (upload / "file_metadata.json").write_text(
            json.dumps({"unexpected": "object"}), encoding="utf-8"
        )
        manager = FileManager(str(upload), str(output))
        assert manager.files == {}

        stored = upload / "file_1.txt"
        stored.write_text("hello", encoding="utf-8")
        manager.register(FileInfo("file_1", "hello.txt", str(stored),
                                  "text", ".txt", stored.stat().st_size))
        reloaded = FileManager(str(upload), str(output))
        assert reloaded.files["file_1"].original_name == "hello.txt"
        assert reloaded.unregister("file_1") is True
        assert FileManager(str(upload), str(output)).files == {}


class TestSharedConfiguration:
    def test_upload_allowlists_have_one_source(self):
        from office_agent.api.core.config import APIConfig
        from office_agent.runtime_config import ALLOWED_UPLOAD_EXTENSIONS
        from office_agent.security.config import SecurityConfig
        from office_agent.storage.validators import ALLOWED_EXTENSIONS

        expected = set(ALLOWED_UPLOAD_EXTENSIONS)
        assert set(APIConfig().allowed_extensions) == expected
        assert set(SecurityConfig().allowed_extensions) == expected
        assert set(ALLOWED_EXTENSIONS) == expected


class TestRepositoryContracts:
    def test_unknown_fields_and_invalid_pages_are_rejected(self):
        from office_agent.database.models import Task
        from office_agent.database.repository.base import BaseRepository

        repository = BaseRepository(None, Task)
        assert repository._page(0, 1000) == (0, 1000)
        with pytest.raises(ValueError, match="offset"):
            repository._page(-1, 10)
        with pytest.raises(ValueError, match="limit"):
            repository._page(0, 1001)
        with pytest.raises(ValueError, match="未知过滤字段"):
            repository._column("typo_field")

    def test_running_execution_has_no_fake_end_time(self):
        from office_agent.database.repository.execution_repo import ExecutionLogRepository

        repository = ExecutionLogRepository(None)
        repository.create = lambda value: value
        running = repository.log_execution("word", status="running")
        assert running.start_time.tzinfo is not None
        assert running.end_time is None


class TestSecurityDepth:
    def test_sandbox_ast_blocks_whitespace_import_and_dynamic_import(self, temp_dir):
        from office_agent.security.sandbox import Sandbox, SandboxStatus

        sandbox = Sandbox(work_dir=temp_dir, allow_unsafe_subprocess=True)
        assert sandbox.execute("import  os").status == SandboxStatus.BLOCKED
        assert sandbox.execute("result = __import__('os')").status == SandboxStatus.BLOCKED
        sandbox.cleanup()
        assert temp_dir.exists(), "不得删除调用方提供的目录"

    def test_unknown_tool_is_critical_and_rate_count_is_thread_safe(self):
        from office_agent.security.permission.agent_permissions import (
            AgentPermissionManager, RiskLevel, ToolInfo, ToolRegistry,
        )

        manager = AgentPermissionManager()
        assert manager.check_tool_risk("unregistered") == RiskLevel.CRITICAL

        tool = ToolInfo("limited", "limited", RiskLevel.LOW,
                        allowed_agents={"word"}, max_calls_per_minute=1)
        registry = ToolRegistry({"limited": tool})
        manager = AgentPermissionManager(registry)
        # Use the existing authoritative agent permission map for this test tool.
        from office_agent.security.permission import agent_permissions as module
        previous = set(module.AGENT_TOOL_PERMISSIONS["word"])
        module.AGENT_TOOL_PERMISSIONS["word"].add("limited")
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(
                    lambda _index: manager.can_use_tool("word", "limited")[0], range(8)
                ))
            assert sum(results) == 1
        finally:
            module.AGENT_TOOL_PERMISSIONS["word"] = previous
