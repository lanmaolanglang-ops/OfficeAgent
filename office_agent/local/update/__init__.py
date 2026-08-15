"""
Local Update - 升级管理
"""
from .update_manager import (
    UpdateStatus, VersionInfo, UpdateProgress, UpdateManager,
    CURRENT_VERSION, get_update_manager,
)

__all__ = [
    "UpdateStatus", "VersionInfo", "UpdateProgress", "UpdateManager",
    "CURRENT_VERSION", "get_update_manager",
]
