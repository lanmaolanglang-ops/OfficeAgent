"""Shared, side-effect-free runtime configuration primitives.

Canonical data-root policy (P5-14) — every entry point (backend, worker,
desktop launcher, runtime manager, Windows service wrapper) resolves the
application data root through :func:`resolve_data_root`, so the same
installation always reads and writes one location:

1. ``OFFICE_AGENT_DATA_DIR`` (explicit override; the desktop runtime env
   injects it into the backend process) — highest priority.
2. frozen/installed build — the platform-native per-user application data
   directory (``%APPDATA%\\OfficeAgent`` on Windows).
3. source checkout / development run — ``~/.office_agent``, the historical
   development root, so dev and test runs never touch the installed
   product's real user data.

The resolver never consults the current working directory or the project
root, so the root cannot drift between restarts or working directories.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


ALLOWED_UPLOAD_EXTENSIONS = frozenset({
    ".docx", ".pptx", ".xlsx", ".pdf",
    ".txt", ".md", ".csv", ".json", ".xml",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
})


def _platform_app_data_root() -> Path:
    """Return the platform-native per-user application data root."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home()) / "OfficeAgent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OfficeAgent"
    return Path.home() / ".local" / "share" / "OfficeAgent"


def _source_workspace_root() -> Path:
    """Return the source-checkout / development data root.

    Deliberately not the installed product root: a developer or test run
    must never read or write the real user's data directory.
    """
    return Path.home() / ".office_agent"


def resolve_data_root() -> Path:
    """Resolve the one authoritative application data root without creating it.

    Priority (deterministic): explicit ``OFFICE_AGENT_DATA_DIR`` →
    platform-native root when running as a frozen/installed build →
    ``~/.office_agent`` for a source checkout. Never derived from the
    current working directory or the project root, so the resolved root is
    stable across restarts and working directories.
    """
    configured = os.environ.get("OFFICE_AGENT_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    if getattr(sys, "frozen", False):
        return _platform_app_data_root()
    return _source_workspace_root()


def get_data_root() -> Path:
    """Return the backend/worker data root via the canonical resolver."""
    return resolve_data_root()


def get_desktop_data_root() -> Path:
    """Return the desktop entry-point data root.

    Kept under this name because the desktop launcher, runtime manager and
    service wrapper already import it, but it is now an alias of the same
    canonical resolver — the desktop and the backend can no longer disagree
    about where application data lives.
    """
    return resolve_data_root()


def get_log_dir() -> Path:
    """Resolve the log directory with explicit log overrides taking precedence."""
    # Namespaced override wins over the generic LOG_DIR, which is shared by
    # many unrelated tools (P3-3).
    configured = os.environ.get("OFFICE_AGENT_LOG_DIR") or os.environ.get("LOG_DIR")
    return Path(configured).expanduser() if configured else get_data_root() / "logs"


def get_output_dir() -> Path:
    """Return the shared generated-output directory without creating it."""
    configured = os.environ.get("OFFICE_AGENT_OUTPUT_DIR")
    return Path(configured).expanduser() if configured else get_data_root() / "outputs"


def get_upload_dir() -> Path:
    """Return the shared upload directory without creating it."""
    configured = os.environ.get("OFFICE_AGENT_UPLOAD_DIR")
    return Path(configured).expanduser() if configured else get_data_root() / "uploads"


def desktop_runtime_env(data_dir, log_dir=None, app_version=None) -> dict:
    """Return the authoritative desktop runtime environment mapping.

    Single source for the variables every desktop entry point must provide
    (local mode flags, data/log directories, version). Pure function: does
    not touch os.environ, so callers decide between subprocess env
    (runtime manager) and in-process application (frozen launcher,
    service wrapper).
    """
    data_dir = Path(data_dir)
    env = {
        "OFFICE_AGENT_LOCAL": "1",
        "AUTH_MODE": "local",
        "OFFICE_AGENT_DATA_DIR": str(data_dir),
        "OFFICE_AGENT_LOG_DIR": str(Path(log_dir) if log_dir else data_dir / "logs"),
    }
    if app_version:
        env["OFFICE_AGENT_VERSION"] = str(app_version)
    return env


def apply_desktop_runtime_env(data_dir, log_dir=None, app_version=None) -> dict:
    """Apply desktop_runtime_env() to os.environ and return the applied mapping."""
    env = desktop_runtime_env(data_dir, log_dir=log_dir, app_version=app_version)
    os.environ.update(env)
    return env
