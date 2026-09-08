"""Create and verify the frozen-backend manifest used by Tauri releases.

The manifest binds every file in dist/OfficeAgent to one full Git commit SHA.
Both missing and unexpected files are rejected so a bundle cannot silently mix
artifacts from different backend builds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


MANIFEST_NAME = "release-manifest.json"
PRIMARY_ARTIFACT = "OfficeAgent.exe"
SCHEMA_VERSION = 2
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_RE = re.compile(
    r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE
)


class ManifestError(RuntimeError):
    """Raised when a release artifact cannot be proven trustworthy."""


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ManifestError(f"Git command failed: {detail}")
    return result.stdout.strip()


def _normalize_commit(value: str) -> str:
    commit = value.strip().lower()
    if not _FULL_SHA_RE.fullmatch(commit):
        raise ManifestError("source commit must be a full 40-character Git SHA")
    return commit


def _source_version(repo_root: Path) -> str:
    source = repo_root / "office_agent" / "_version.py"
    try:
        payload = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ManifestError(
            f"application version source is unreadable: {exc}"
        ) from exc
    match = _VERSION_RE.search(payload)
    if not match or not match.group(1).strip():
        raise ManifestError("application version source is invalid")
    return match.group(1).strip()


def _current_commit(repo_root: Path, *, require_clean: bool) -> str:
    commit = _normalize_commit(_run_git(repo_root, "rev-parse", "HEAD"))
    if require_clean:
        dirty = _run_git(
            repo_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
        )
        if dirty:
            raise ManifestError(
                "tracked worktree is dirty; commit release inputs before building"
            )
    return commit


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_files(artifact_dir: Path) -> list[Path]:
    if not artifact_dir.is_dir():
        raise ManifestError(f"backend artifact directory is missing: {artifact_dir}")
    primary = artifact_dir / PRIMARY_ARTIFACT
    if not primary.is_file():
        raise ManifestError(f"primary backend artifact is missing: {primary}")

    files: list[Path] = []
    for path in artifact_dir.rglob("*"):
        if path.name == MANIFEST_NAME:
            continue
        if path.is_symlink():
            raise ManifestError(
                f"symbolic links are not allowed in release artifacts: {path}"
            )
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(artifact_dir).as_posix())


def create_manifest(
        artifact_dir: Path, source_commit: str, app_version: str
) -> dict[str, Any]:
    artifact_dir = artifact_dir.resolve()
    commit = _normalize_commit(source_commit)
    entries = [
        {
            "path": path.relative_to(artifact_dir).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in _artifact_files(artifact_dir)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "source_commit": commit,
        "app_version": app_version,
        "primary_artifact": PRIMARY_ARTIFACT,
        "files": entries,
    }


def write_manifest(artifact_dir: Path, manifest: dict[str, Any]) -> Path:
    artifact_dir = artifact_dir.resolve()
    destination = artifact_dir / MANIFEST_NAME
    payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{MANIFEST_NAME}.",
        suffix=".tmp",
        dir=artifact_dir,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def _load_manifest(artifact_dir: Path) -> dict[str, Any]:
    path = artifact_dir / MANIFEST_NAME
    if not path.is_file():
        raise ManifestError(f"release manifest is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"release manifest is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestError("release manifest root must be an object")
    return value


def verify_manifest(
        artifact_dir: Path, expected_commit: str, expected_version: str
) -> dict[str, Any]:
    artifact_dir = artifact_dir.resolve()
    expected = _normalize_commit(expected_commit)
    manifest = _load_manifest(artifact_dir)

    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError("release manifest schema version is unsupported")
    if manifest.get("source_commit") != expected:
        raise ManifestError(
            "backend source commit mismatch: "
            f"manifest={manifest.get('source_commit')!r}, expected={expected}"
        )
    if manifest.get("app_version") != expected_version:
        raise ManifestError(
            "backend application version mismatch: "
            f"manifest={manifest.get('app_version')!r}, expected={expected_version!r}"
        )
    if manifest.get("primary_artifact") != PRIMARY_ARTIFACT:
        raise ManifestError("release manifest primary artifact is invalid")

    raw_entries = manifest.get("files")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ManifestError("release manifest files must be a non-empty array")

    entries: dict[str, dict[str, Any]] = {}
    for item in raw_entries:
        if not isinstance(item, dict):
            raise ManifestError("release manifest contains a non-object file entry")
        relative = item.get("path")
        if not isinstance(relative, str) or not relative or relative in entries:
            raise ManifestError(
                "release manifest contains an invalid or duplicate path"
            )
        normalized = Path(relative)
        if normalized.is_absolute() or ".." in normalized.parts or "\\" in relative:
            raise ManifestError(f"release manifest path is unsafe: {relative!r}")
        entries[relative] = item

    actual_paths = {
        path.relative_to(artifact_dir).as_posix(): path
        for path in _artifact_files(artifact_dir)
    }
    if set(entries) != set(actual_paths):
        missing = sorted(set(entries) - set(actual_paths))
        unexpected = sorted(set(actual_paths) - set(entries))
        raise ManifestError(
            f"backend file set mismatch: missing={missing}, unexpected={unexpected}"
        )

    for relative, path in actual_paths.items():
        entry = entries[relative]
        expected_size = entry.get("size")
        expected_hash = entry.get("sha256")
        if not isinstance(expected_size, int) or expected_size < 0:
            raise ManifestError(f"invalid size in manifest: {relative}")
        if not isinstance(expected_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_hash
        ):
            raise ManifestError(f"invalid SHA-256 in manifest: {relative}")
        if path.stat().st_size != expected_size:
            raise ManifestError(f"backend artifact size mismatch: {relative}")
        if _sha256(path) != expected_hash:
            raise ManifestError(f"backend artifact hash mismatch: {relative}")

    if PRIMARY_ARTIFACT not in entries:
        raise ManifestError("primary backend artifact is absent from manifest")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or verify the OfficeAgent frozen-backend release manifest."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument(
            "--artifact-dir",
            type=Path,
            default=Path("dist") / "OfficeAgent",
        )
        subparser.add_argument(
            "--repo-root",
            type=Path,
            default=Path(__file__).resolve().parents[1],
        )
        option = "--source-commit" if command == "create" else "--expected-commit"
        subparser.add_argument(option)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        explicit_commit = (
            args.source_commit if args.command == "create" else args.expected_commit
        )
        commit = (
            _normalize_commit(explicit_commit)
            if explicit_commit
            else _current_commit(args.repo_root.resolve(), require_clean=True)
        )
        version = _source_version(args.repo_root.resolve())
        if args.command == "create":
            manifest = create_manifest(args.artifact_dir, commit, version)
            path = write_manifest(args.artifact_dir, manifest)
            print(f"created {path} for {commit} ({len(manifest['files'])} files)")
        else:
            manifest = verify_manifest(args.artifact_dir, commit, version)
            print(
                f"verified {args.artifact_dir.resolve()} for {commit} "
                f"({len(manifest['files'])} files)"
            )
    except ManifestError as exc:
        print(f"release manifest check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
