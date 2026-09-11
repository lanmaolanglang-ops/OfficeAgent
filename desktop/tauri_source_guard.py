"""Run the Tauri build while containing its known generated-file rewrites.

Tauri 2 regenerates capability schemas during a clean build and its CLI can
temporarily rewrite Cargo.toml. Release inputs must start and finish at the
same clean Git commit, so this guard snapshots those exact tracked files,
allows no other tracked mutation, and restores their original bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


ALLOWED_TAURI_MUTATIONS = (
    "desktop-client/src-tauri/Cargo.toml",
    "desktop-client/src-tauri/gen/schemas/desktop-schema.json",
    "desktop-client/src-tauri/gen/schemas/windows-schema.json",
)


class GuardError(RuntimeError):
    """Raised when the guarded build cannot preserve release provenance."""


def _run_git(repo_root: Path, *args: str, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
        text=not binary,
        encoding=None if binary else "utf-8",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        raise GuardError(f"Git command failed: {detail}")
    return result.stdout


def _changed_tracked_paths(repo_root: Path) -> set[str]:
    raw = _run_git(
        repo_root,
        "diff",
        "--name-only",
        "-z",
        "HEAD",
        "--",
        binary=True,
    )
    assert isinstance(raw, bytes)
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in raw.split(b"\0")
        if item
    }


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _restore_bytes(path: Path, payload: bytes, mode: int) -> None:
    if path.exists() and path.is_dir():
        raise GuardError(f"cannot restore tracked file over a directory: {path}")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _platform_command(command: Sequence[str]) -> list[str]:
    if not command:
        raise GuardError("a build command is required")
    executable = shutil.which(command[0])
    if executable is None:
        raise GuardError(f"build command is unavailable: {command[0]}")
    resolved = [executable, *command[1:]]
    if os.name == "nt" and Path(executable).suffix.lower() in {".bat", ".cmd"}:
        command_line = subprocess.list2cmdline(resolved)
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command_line]
    return resolved


def run_guarded(
    repo_root: Path,
    command: Sequence[str],
    *,
    allowed_paths: Sequence[str] = ALLOWED_TAURI_MUTATIONS,
) -> int:
    repo_root = repo_root.resolve()
    initial_changes = _changed_tracked_paths(repo_root)
    if initial_changes:
        paths = "\n".join(sorted(initial_changes))
        raise GuardError(f"tracked worktree must be clean before Tauri build:\n{paths}")

    snapshots: dict[str, tuple[bytes, int]] = {}
    for relative in allowed_paths:
        path = repo_root / relative
        if not path.is_file() or path.is_symlink():
            raise GuardError(f"guarded tracked file is missing or unsafe: {relative}")
        tracked = _run_git(repo_root, "ls-files", "--error-unmatch", "--", relative)
        if not str(tracked).strip():
            raise GuardError(f"guarded file is not tracked: {relative}")
        snapshots[relative] = (path.read_bytes(), path.stat().st_mode)

    completed: subprocess.CompletedProcess[bytes] | None = None
    changed: set[str] = set()
    try:
        completed = subprocess.run(
            _platform_command(command),
            check=False,
            cwd=repo_root,
        )
    finally:
        changed = _changed_tracked_paths(repo_root)
        for relative, (payload, mode) in snapshots.items():
            if relative not in changed:
                continue
            path = repo_root / relative
            after = path.read_bytes() if path.is_file() else b""
            print(
                "[TAURI-GUARD] restoring generated rewrite "
                f"{relative} ({_sha256(payload)[:12]} -> {_sha256(after)[:12]})"
            )
            _restore_bytes(path, payload, mode)

    unexpected = changed - set(allowed_paths)
    remaining = _changed_tracked_paths(repo_root)
    if unexpected:
        paths = "\n".join(sorted(unexpected))
        raise GuardError(f"Tauri build changed unexpected tracked files:\n{paths}")
    if remaining:
        paths = "\n".join(sorted(remaining))
        raise GuardError(f"tracked files remain changed after restoration:\n{paths}")
    if completed is None:
        raise GuardError("Tauri build did not start")
    if completed.returncode != 0:
        print(
            f"[TAURI-GUARD] build command failed with exit code {completed.returncode}",
            file=sys.stderr,
        )
    return completed.returncode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Tauri with strict containment of generated tracked rewrites."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        return run_guarded(args.repo_root, command)
    except GuardError as exc:
        print(f"Tauri source guard failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
