from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from desktop import tauri_source_guard


ALLOWED = "generated.json"
UNEXPECTED = "source.txt"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "RC Guard Test")
    _git(tmp_path, "config", "user.email", "rc-guard@example.invalid")
    (tmp_path / ALLOWED).write_text("canonical\n", encoding="utf-8")
    (tmp_path / UNEXPECTED).write_text("source\n", encoding="utf-8")
    _git(tmp_path, "add", ALLOWED, UNEXPECTED)
    _git(tmp_path, "commit", "-m", "fixture")
    return tmp_path


def _python_write(path: str, payload: str, *, exit_code: int = 0) -> list[str]:
    script = (
        "from pathlib import Path; "
        f"Path({path!r}).write_text({payload!r}, encoding='utf-8'); "
        f"raise SystemExit({exit_code})"
    )
    return [sys.executable, "-c", script]


def test_known_tauri_rewrite_is_restored(clean_repo: Path):
    result = tauri_source_guard.run_guarded(
        clean_repo,
        _python_write(ALLOWED, "generated\n"),
        allowed_paths=[ALLOWED],
    )

    assert result == 0
    assert (clean_repo / ALLOWED).read_text(encoding="utf-8") == "canonical\n"
    assert _git(clean_repo, "status", "--short") == ""


def test_failed_build_still_restores_known_rewrite(clean_repo: Path):
    result = tauri_source_guard.run_guarded(
        clean_repo,
        _python_write(ALLOWED, "generated\n", exit_code=7),
        allowed_paths=[ALLOWED],
    )

    assert result == 7
    assert (clean_repo / ALLOWED).read_text(encoding="utf-8") == "canonical\n"
    assert _git(clean_repo, "status", "--short") == ""


def test_unexpected_tracked_rewrite_is_blocking(clean_repo: Path):
    command = _python_write(UNEXPECTED, "changed\n")

    with pytest.raises(tauri_source_guard.GuardError, match=UNEXPECTED):
        tauri_source_guard.run_guarded(
            clean_repo,
            command,
            allowed_paths=[ALLOWED],
        )

    assert _git(clean_repo, "status", "--short") == f"M {UNEXPECTED}"


def test_dirty_tree_is_rejected_before_command_runs(clean_repo: Path):
    marker = clean_repo / "command-ran.txt"
    (clean_repo / UNEXPECTED).write_text("dirty\n", encoding="utf-8")

    with pytest.raises(tauri_source_guard.GuardError, match=UNEXPECTED):
        tauri_source_guard.run_guarded(
            clean_repo,
            _python_write(str(marker), "ran\n"),
            allowed_paths=[ALLOWED],
        )

    assert not marker.exists()


def test_status_parser_includes_both_rename_paths(clean_repo: Path):
    renamed = "renamed-source.txt"
    _git(clean_repo, "mv", UNEXPECTED, renamed)

    assert tauri_source_guard._changed_tracked_paths(clean_repo) == {
        UNEXPECTED,
        renamed,
    }
