"""Shared, side-effect-free runtime configuration primitives."""
from __future__ import annotations

import os
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
