"""Shared, side-effect-free runtime configuration primitives."""
from __future__ import annotations

import os
import sys
from pathlib import Path


ALLOWED_UPLOAD_EXTENSIONS = frozenset({
    ".docx", ".pptx", ".xlsx", ".pdf",
    ".txt", ".md", ".csv", ".json", ".xml",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
})


def get_data_root() -> Path:
    """Return the one authoritative application data root without creating it."""
    configured = os.environ.get("OFFICE_AGENT_DATA_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".office_agent"


def get_desktop_data_root() -> Path:
    """Return the platform-native desktop data root, honoring an explicit override."""
    configured = os.environ.get("OFFICE_AGENT_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home()) / "OfficeAgent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OfficeAgent"
    return Path.home() / ".local" / "share" / "OfficeAgent"


def get_log_dir() -> Path:
    """Resolve the log directory with explicit log overrides taking precedence."""
    configured = os.environ.get("LOG_DIR") or os.environ.get("OFFICE_AGENT_LOG_DIR")
    return Path(configured).expanduser() if configured else get_data_root() / "logs"


def get_output_dir() -> Path:
    """Return the shared generated-output directory without creating it."""
    configured = os.environ.get("OFFICE_AGENT_OUTPUT_DIR")
    return Path(configured).expanduser() if configured else get_data_root() / "outputs"


def get_upload_dir() -> Path:
    """Return the shared upload directory without creating it."""
    configured = os.environ.get("OFFICE_AGENT_UPLOAD_DIR")
    return Path(configured).expanduser() if configured else get_data_root() / "uploads"
