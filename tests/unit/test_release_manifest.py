from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from office_agent._version import __version__


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "desktop" / "release_manifest.py"
CURRENT_SHA = "a" * 40
STALE_SHA = "b" * 40


def test_release_version_sources_are_synchronized():
    package = json.loads(
        (ROOT / "desktop-client" / "package.json").read_text(encoding="utf-8")
    )
    tauri = json.loads(
        (ROOT / "desktop-client" / "src-tauri" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    cargo = (
        ROOT / "desktop-client" / "src-tauri" / "Cargo.toml"
    ).read_text(encoding="utf-8")
    cargo_version = re.search(
        r'^version\s*=\s*"([^"]+)"\s*$', cargo, re.MULTILINE
    )

    assert cargo_version is not None
    assert package["version"] == __version__
    assert tauri["version"] == __version__
    assert cargo_version.group(1) == __version__


def test_pyinstaller_spec_bundles_alembic_runtime_resources():
    spec = (ROOT / "office_agent.spec").read_text(encoding="utf-8")

    assert "database_dir / 'alembic.ini'" in spec
    assert "database_dir / 'migrations'" in spec
    assert "'office_agent/database/migrations'" in spec


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def _artifact_tree(tmp_path: Path) -> Path:
    artifact_dir = tmp_path / "OfficeAgent"
    (artifact_dir / "_internal").mkdir(parents=True)
    (artifact_dir / "OfficeAgent.exe").write_bytes(b"frozen-backend")
    (artifact_dir / "_internal" / "runtime.bin").write_bytes(b"runtime")
    return artifact_dir


def test_manifest_is_deterministic_and_current_sha_verifies(tmp_path: Path):
    artifact_dir = _artifact_tree(tmp_path)
    create = _run(
        "create",
        "--artifact-dir",
        str(artifact_dir),
        "--source-commit",
        CURRENT_SHA,
    )
    assert create.returncode == 0, create.stderr

    manifest_path = artifact_dir / "release-manifest.json"
    first_payload = manifest_path.read_bytes()
    recreate = _run(
        "create",
        "--artifact-dir",
        str(artifact_dir),
        "--source-commit",
        CURRENT_SHA,
    )
    assert recreate.returncode == 0, recreate.stderr
    assert manifest_path.read_bytes() == first_payload

    manifest = json.loads(first_payload)
    assert manifest["source_commit"] == CURRENT_SHA
    assert manifest["app_version"] == __version__
    assert [item["path"] for item in manifest["files"]] == [
        "OfficeAgent.exe",
        "_internal/runtime.bin",
    ]

    verify = _run(
        "verify",
        "--artifact-dir",
        str(artifact_dir),
        "--expected-commit",
        CURRENT_SHA,
    )
    assert verify.returncode == 0, verify.stderr
    assert "verified" in verify.stdout


def test_stale_source_sha_is_a_blocking_failure(tmp_path: Path):
    artifact_dir = _artifact_tree(tmp_path)
    assert (
        _run(
            "create",
            "--artifact-dir",
            str(artifact_dir),
            "--source-commit",
            STALE_SHA,
        ).returncode
        == 0
    )

    verify = _run(
        "verify",
        "--artifact-dir",
        str(artifact_dir),
        "--expected-commit",
        CURRENT_SHA,
    )
    assert verify.returncode != 0
    assert "source commit mismatch" in verify.stderr


def test_stale_application_version_is_a_blocking_failure(tmp_path: Path):
    artifact_dir = _artifact_tree(tmp_path)
    create = _run(
        "create", "--artifact-dir", str(artifact_dir),
        "--source-commit", CURRENT_SHA,
    )
    assert create.returncode == 0, create.stderr
    manifest_path = artifact_dir / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["app_version"] = "0.0.0-stale"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verify = _run(
        "verify", "--artifact-dir", str(artifact_dir),
        "--expected-commit", CURRENT_SHA,
    )
    assert verify.returncode != 0
    assert "application version mismatch" in verify.stderr


def test_tampered_or_unexpected_backend_files_are_blocking(tmp_path: Path):
    artifact_dir = _artifact_tree(tmp_path)
    assert (
        _run(
            "create",
            "--artifact-dir",
            str(artifact_dir),
            "--source-commit",
            CURRENT_SHA,
        ).returncode
        == 0
    )

    (artifact_dir / "OfficeAgent.exe").write_bytes(b"tampered")
    tampered = _run(
        "verify",
        "--artifact-dir",
        str(artifact_dir),
        "--expected-commit",
        CURRENT_SHA,
    )
    assert tampered.returncode != 0
    assert "mismatch" in tampered.stderr

    assert (
        _run(
            "create",
            "--artifact-dir",
            str(artifact_dir),
            "--source-commit",
            CURRENT_SHA,
        ).returncode
        == 0
    )
    (artifact_dir / "_internal" / "unexpected.bin").write_bytes(b"old")
    unexpected = _run(
        "verify",
        "--artifact-dir",
        str(artifact_dir),
        "--expected-commit",
        CURRENT_SHA,
    )
    assert unexpected.returncode != 0
    assert "file set mismatch" in unexpected.stderr
