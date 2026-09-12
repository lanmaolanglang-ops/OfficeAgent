"""P1-23：安装自测必须区分"存在 / 可用 / 符合契约"，且不得默认成功。

修复前行为（旧源码副本实测）：
- ``test_file_upload`` 请求 ``/api/v1/files/upload``（真实路由是 ``/api/file/upload``），
  并把 **404 当作"上传 API 响应正常"判 PASS**；路由被删掉自测也永远通过；
  且部分分支不 return，``run_all`` 把 ``None`` 记成 PASS("OK")。
- ``except urllib.error.HTTPError`` 依赖 ``import urllib.request`` 的隐式副作用。
- ``test_health_endpoint`` 用 ``data.get("data", {}).get("version")`` 读一个**不存在**
  的键（真实 /health 是扁平结构），且把"含 data 字段"当成健康。
- ``test_dependencies`` 不分必需/可选，缺少构建期依赖也判 FAIL。
- 源码树判据（目录/文件存在）在发布安装态下同样被当作"安装成功"。

全部 mock 网络与 subprocess，不真的启动后端、不卸载/破坏当前环境。
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import urllib.error
import urllib.request

import pytest

from tests.test_installation import (
    PRIMARY_ARTIFACT,
    REQUIREMENT_IMPORT_NAMES,
    UPLOAD_ROUTE,
    InstallationTestSuite,
    TestStatus,
    parse_production_requirements,
)


class _FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._body = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakePopen:
    def __init__(self, returncode=None, stderr: bytes = b""):
        self.pid = 4242
        self._returncode = returncode
        self._stderr = stderr
        self.terminated = False

    def poll(self):
        return self._returncode

    def communicate(self):
        return b"", self._stderr

    def terminate(self):
        self.terminated = True
        self._returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._returncode = -9


class _FakeSocket:
    def __init__(self, connect_result: int):
        self._connect_result = connect_result

    def connect_ex(self, address):
        return self._connect_result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:8765", code, "err", None, io.BytesIO(b""),
    )


def _suite(tmp_path, mode="installed") -> InstallationTestSuite:
    return InstallationTestSuite(app_dir=tmp_path, port=8765, mode=mode)


def _write_installed_artifacts(tmp_path, *, magic=b"MZ", manifest=True, as_dir=False):
    artifact = tmp_path / PRIMARY_ARTIFACT
    if as_dir:
        artifact.mkdir(parents=True, exist_ok=True)
    else:
        artifact.write_bytes(magic + b"\x00" * 8)
    if manifest:
        (tmp_path / "release-manifest.json").write_text("{}", encoding="utf-8")
    return artifact


def _write_source_tree(tmp_path):
    (tmp_path / "office_agent" / "api").mkdir(parents=True, exist_ok=True)
    (tmp_path / "office_agent" / "excel_agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "office_agent" / "ppt_agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "office_agent" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "office_agent" / "api" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "requirements-production.txt").write_text("fastapi==0.115.0\n", encoding="utf-8")


class TestInstallLayoutContract:
    def test_detect_mode_source_tree(self, tmp_path):
        _write_source_tree(tmp_path)
        assert InstallationTestSuite.detect_mode(tmp_path) == "source"

    def test_detect_mode_installed_layout(self, tmp_path):
        _write_installed_artifacts(tmp_path)
        assert InstallationTestSuite.detect_mode(tmp_path) == "installed"

    def test_detect_mode_unknown(self, tmp_path):
        assert InstallationTestSuite.detect_mode(tmp_path) == "unknown"

    def test_installed_artifacts_pass(self, tmp_path):
        _write_installed_artifacts(tmp_path)
        result = _suite(tmp_path).test_install_layout()
        assert result.status is TestStatus.PASS
        assert PRIMARY_ARTIFACT in result.message

    def test_missing_artifact_fails(self, tmp_path):
        result = _suite(tmp_path).test_install_layout()
        assert result.status is TestStatus.FAIL

    def test_artifact_is_a_directory_fails(self, tmp_path):
        _write_installed_artifacts(tmp_path, as_dir=True)
        result = _suite(tmp_path).test_install_layout()
        assert result.status is TestStatus.FAIL
        assert "不是普通文件" in result.message

    def test_artifact_with_wrong_magic_fails(self, tmp_path):
        _write_installed_artifacts(tmp_path, magic=b"#!")
        result = _suite(tmp_path).test_install_layout()
        assert result.status is TestStatus.FAIL
        assert "不是可执行文件" in result.message

    def test_missing_manifest_is_warn_not_pass(self, tmp_path):
        _write_installed_artifacts(tmp_path, manifest=False)
        result = _suite(tmp_path).test_install_layout()
        assert result.status is TestStatus.WARN

    def test_source_tree_without_artifact_skips_instead_of_passing(self, tmp_path):
        _write_source_tree(tmp_path)
        result = _suite(tmp_path, mode="source").test_install_layout()
        assert result.status is TestStatus.SKIP
        assert result.status is not TestStatus.PASS

    def test_source_checks_are_not_passed_in_installed_mode(self, tmp_path):
        """开发源码判据不适用于发布安装态：显式 WARN，不得伪造成 PASS。"""
        _write_installed_artifacts(tmp_path)
        suite = _suite(tmp_path)
        for check in (suite.test_directory_structure, suite.test_config_files):
            result = check()
            assert result.status is TestStatus.WARN
            assert "不适用" in result.message

    def test_source_checks_fail_when_source_artifacts_missing(self, tmp_path):
        suite = _suite(tmp_path, mode="source")
        assert suite.test_directory_structure().status is TestStatus.FAIL
        assert suite.test_config_files().status is TestStatus.FAIL

    def test_source_checks_pass_on_complete_source_tree(self, tmp_path):
        _write_source_tree(tmp_path)
        suite = _suite(tmp_path, mode="source")
        assert suite.test_directory_structure().status is TestStatus.PASS
        assert suite.test_config_files().status is TestStatus.PASS


class TestDependencyContract:
    def test_required_set_comes_from_production_requirements(self, tmp_path):
        (tmp_path / "requirements-production.txt").write_text(
            "# 注释\n"
            "fastapi==0.115.0\n"
            "\n"
            "uvicorn[standard]==0.30.6\n"
            "pyinstaller==6.21.0\n"
            'pywin32==306; sys_platform == "win32"\n',
            encoding="utf-8",
        )
        required, optional = _suite(tmp_path).resolve_dependencies()
        assert "fastapi" in required
        assert "uvicorn" in required
        assert "pyinstaller" in optional  # 构建期依赖不是运行期必需
        assert "pywin32" not in required or os.name == "nt"

    def test_missing_required_dependency_fails(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(suite, "resolve_dependencies", lambda: (["fastapi"], ["pyinstaller"]))
        monkeypatch.setattr(suite, "_missing_imports", lambda names: ["fastapi"] if "fastapi" in names else [])
        result = suite.test_dependencies()
        assert result.status is TestStatus.FAIL
        assert "fastapi" in result.message

    def test_missing_optional_dependency_is_warn_not_fail(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(suite, "resolve_dependencies", lambda: (["fastapi"], ["pyinstaller"]))
        monkeypatch.setattr(suite, "_missing_imports", lambda names: ["pyinstaller"] if "pyinstaller" in names else [])
        result = suite.test_dependencies()
        assert result.status is TestStatus.WARN
        assert "可选依赖" in result.message

    def test_all_dependencies_present_passes(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(suite, "resolve_dependencies", lambda: (["fastapi"], ["pyinstaller"]))
        monkeypatch.setattr(suite, "_missing_imports", lambda names: [])
        assert suite.test_dependencies().status is TestStatus.PASS

    def test_fallback_required_set_when_contract_file_absent(self, tmp_path):
        required, optional = _suite(tmp_path).resolve_dependencies()
        assert "fastapi" in required
        assert "python-docx" in required
        assert optional == []


class TestParseProductionRequirements:
    def test_parses_names_and_units(self):
        text = (
            "# header\n"
            "fastapi==0.115.0\n"
            "uvicorn[standard]==0.30.6\n"
            "python-docx==1.1.2\n"
            'pywin32==306; sys_platform == "win32"\n'
            "  \n"
            "# trailing comment\n"
        )
        assert parse_production_requirements(text) == [
            "fastapi", "uvicorn", "python-docx", "pywin32",
        ]

    def test_import_name_mapping_covers_the_real_production_contract(self):
        """契约文件里的每个发行名都必须有 import 名映射，否则依赖检查读错模块。"""
        from pathlib import Path

        contract = Path(__file__).resolve().parents[2] / "requirements-production.txt"
        names = parse_production_requirements(contract.read_text(encoding="utf-8"))
        assert names, "生产依赖契约不应为空"
        unmapped = [n for n in names if n.lower() not in REQUIREMENT_IMPORT_NAMES]
        assert unmapped == [], f"以下发行名缺少 import 名映射: {unmapped}"


class TestBackendStartupVerdicts:
    def test_port_occupied_by_foreign_service_fails(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(socket, "socket", lambda *a, **k: _FakeSocket(0))
        monkeypatch.setattr(
            suite, "_get_json",
            lambda path, timeout=5.0: (200, {"status": "ok", "note": "someone else"}),
        )
        result = suite.test_backend_startup()
        assert result.status is TestStatus.FAIL
        assert "非本产品" in result.message

    def test_port_occupied_by_our_healthy_backend_passes(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(socket, "socket", lambda *a, **k: _FakeSocket(0))
        monkeypatch.setattr(
            suite, "_get_json",
            lambda path, timeout=5.0: (200, {"status": "healthy", "version": "9.9.9"}),
        )
        assert suite.test_backend_startup().status is TestStatus.PASS

    def test_launch_failure_reports_returncode(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(socket, "socket", lambda *a, **k: _FakeSocket(1))
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakePopen(returncode=3, stderr=b"boom"))
        monkeypatch.setattr("time.sleep", lambda _s: None)
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("refused")),
        )
        result = suite.test_backend_startup()
        assert result.status is TestStatus.FAIL
        assert "returncode=3" in result.message
        assert "boom" in result.message


class TestHealthContract:
    def test_healthy_with_matching_version_passes(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(
            suite, "_get_json",
            lambda path, timeout=5.0: (200, {"status": "healthy", "version": suite._expected_version()}),
        )
        assert suite.test_health_endpoint().status is TestStatus.PASS

    def test_version_mismatch_fails(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(
            suite, "_get_json",
            lambda path, timeout=5.0: (200, {"status": "healthy", "version": "0.0.1-not-ours"}),
        )
        result = suite.test_health_endpoint()
        assert result.status is TestStatus.FAIL
        assert "版本不符" in result.message

    def test_degraded_reports_component_reason(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(
            suite, "_get_json",
            lambda path, timeout=5.0: (200, {
                "status": "degraded",
                "checks": {"database": {"status": "unhealthy", "message": "down"}},
            }),
        )
        result = suite.test_health_endpoint()
        assert result.status is TestStatus.FAIL
        assert "database" in result.message

    def test_list_shaped_data_field_does_not_crash(self, tmp_path, monkeypatch):
        """旧实现用 .get 读 data，data 是 list 时抛 AttributeError。"""
        suite = _suite(tmp_path)
        monkeypatch.setattr(
            suite, "_get_json",
            lambda path, timeout=5.0: (200, {"status": "unhealthy", "data": [1, 2, 3]}),
        )
        result = suite.test_health_endpoint()
        assert result.status is TestStatus.FAIL

    def test_non_dict_payload_fails_without_exception(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(suite, "_get_json", lambda path, timeout=5.0: (200, ["nope"]))
        assert suite.test_health_endpoint().status is TestStatus.FAIL


class TestReadyGateContract:
    def test_ready_true_passes(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(suite, "_port_in_use", lambda: True)
        monkeypatch.setattr(suite, "_get_json", lambda path, timeout=5.0: (200, {"ready": True}))
        assert suite.test_ready_endpoint().status is TestStatus.PASS

    def test_not_ready_is_warn(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(suite, "_port_in_use", lambda: True)
        monkeypatch.setattr(suite, "_get_json", lambda path, timeout=5.0: (503, {"ready": False}))
        assert suite.test_ready_endpoint().status is TestStatus.WARN

    def test_inconsistent_ready_response_fails(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(suite, "_port_in_use", lambda: True)
        monkeypatch.setattr(suite, "_get_json", lambda path, timeout=5.0: (200, {"ready": False}))
        assert suite.test_ready_endpoint().status is TestStatus.FAIL

    def test_backend_absent_skips(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        monkeypatch.setattr(suite, "_port_in_use", lambda: False)
        assert suite.test_ready_endpoint().status is TestStatus.SKIP


class TestUploadVerdicts:
    def _capture_request(self, monkeypatch, response):
        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        return captured

    def test_404_is_failure_not_success(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        self._capture_request(monkeypatch, _http_error(404))
        result = suite.test_file_upload()
        assert result.status is TestStatus.FAIL
        assert "404" in result.message

    def test_uses_the_real_upload_route(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        captured = self._capture_request(
            monkeypatch,
            _FakeResponse(200, {"success": True, "data": {"file_id": "f-1"}}),
        )
        suite.test_file_upload()
        assert UPLOAD_ROUTE in captured["url"]
        assert "/api/v1/files/upload" not in captured["url"]

    def test_success_with_file_id_passes(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        self._capture_request(
            monkeypatch,
            _FakeResponse(200, {"success": True, "data": {"file_id": "f-1"}}),
        )
        result = suite.test_file_upload()
        assert result.status is TestStatus.PASS
        assert "f-1" in result.message

    def test_success_without_file_id_fails(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        self._capture_request(monkeypatch, _FakeResponse(200, {"success": True, "data": {}}))
        assert suite.test_file_upload().status is TestStatus.FAIL

    def test_non_json_body_fails(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        self._capture_request(monkeypatch, _FakeResponse(200, None))
        assert suite.test_file_upload().status is TestStatus.FAIL

    def test_unexpected_status_fails(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        self._capture_request(monkeypatch, _FakeResponse(204, None))
        assert suite.test_file_upload().status is TestStatus.FAIL


class TestRunAllFailsClosed:
    CHECK_NAMES = (
        "test_install_layout", "test_python_runtime", "test_dependencies",
        "test_directory_structure", "test_config_files", "test_backend_startup",
        "test_health_endpoint", "test_ready_endpoint", "test_file_upload",
        "test_word_processing", "test_excel_processing", "test_ppt_processing",
        "test_backend_shutdown",
    )

    def _stub_suite(self, tmp_path, monkeypatch):
        suite = _suite(tmp_path)
        from tests.test_installation import InstallTestResult

        stub = lambda: InstallTestResult(status=TestStatus.PASS, message="ok")
        for name in self.CHECK_NAMES:
            monkeypatch.setattr(suite, name, stub, raising=False)
        return suite

    def test_check_returning_none_is_fail_not_pass(self, tmp_path, monkeypatch):
        suite = self._stub_suite(tmp_path, monkeypatch)
        monkeypatch.setattr(suite, "test_dependencies", lambda: None)
        results = suite.run_all()

        assert len(results) == len(self.CHECK_NAMES)
        dependencies = next(r for r in results if "核心依赖" in r.name)
        assert dependencies.status is TestStatus.FAIL
        assert all(r.name for r in results)

    def test_summary_blocks_release_on_any_failure(self, tmp_path, monkeypatch):
        suite = self._stub_suite(tmp_path, monkeypatch)
        monkeypatch.setattr(suite, "test_dependencies", lambda: None)
        suite.run_all()
        summary = suite.get_summary()
        assert summary["failed"] >= 1
        assert summary["can_release"] is False

    def test_summary_allows_release_when_everything_passes(self, tmp_path, monkeypatch):
        suite = self._stub_suite(tmp_path, monkeypatch)
        suite.run_all()
        summary = suite.get_summary()
        assert summary["failed"] == 0
        assert summary["can_release"] is True
