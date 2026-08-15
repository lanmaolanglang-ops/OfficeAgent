"""
Update Manager - 升级管理器
支持检查版本、下载更新、升级客户端
（框架设计，实际更新服务器地址可配置）
"""
import os
import json
import time
import tempfile
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass, field
from enum import Enum


class UpdateStatus(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    UPDATE_AVAILABLE = "update_available"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    INSTALLING = "installing"
    UP_TO_DATE = "up_to_date"
    ERROR = "error"


@dataclass
class VersionInfo:
    """版本信息"""
    version: str
    release_date: str = ""
    release_notes: str = ""
    download_url: str = ""
    file_size: int = 0
    checksum: str = ""
    is_mandatory: bool = False
    min_version: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "release_date": self.release_date,
            "release_notes": self.release_notes,
            "download_url": self.download_url,
            "file_size": self.file_size,
            "checksum": self.checksum,
            "is_mandatory": self.is_mandatory,
            "min_version": self.min_version,
        }


@dataclass
class UpdateProgress:
    """更新进度"""
    status: UpdateStatus
    progress: float = 0.0
    message: str = ""
    speed: float = 0.0  # bytes/sec
    downloaded: int = 0
    total: int = 0
    error: str = ""


CURRENT_VERSION = "0.49.0"


class UpdateManager:
    """
    升级管理器
    - 检查新版本
    - 下载更新包
    - 执行升级
    - 更新历史记录
    """

    def __init__(self, update_url: str = None, current_version: str = None):
        self._update_url = update_url or "https://api.officeagent.local/v1/updates"
        self._current_version = current_version or CURRENT_VERSION
        self._status = UpdateStatus.IDLE
        self._progress = UpdateProgress(status=UpdateStatus.IDLE)
        self._latest_version: Optional[VersionInfo] = None
        self._download_path: Optional[Path] = None
        self._history: list[dict] = []
        self._callbacks: list[Callable] = []
        self._load_history()

    def _load_history(self) -> None:
        history_path = self._get_history_path()
        if history_path.exists():
            try:
                with open(history_path, "r", encoding="utf-8") as f:
                    self._history = json.load(f)
            except Exception:
                self._history = []

    def _save_history(self) -> None:
        history_path = self._get_history_path()
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(self._history, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _get_history_path() -> Path:
        if os.name == "nt":
            base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OfficeAgent")
        elif os.path.exists("/Applications"):
            base = os.path.expanduser("~/Library/Application Support/OfficeAgent")
        else:
            base = os.path.expanduser("~/.local/share/OfficeAgent")
        return Path(base) / "config" / "update_history.json"

    def on_progress(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def _notify(self) -> None:
        for cb in self._callbacks:
            try:
                cb(self._progress)
            except Exception:
                pass

    @staticmethod
    def parse_version(version: str) -> tuple[int, ...]:
        """Parse a semantic version string."""
        try:
            return tuple(int(x) for x in version.lstrip("vV").split("."))
        except Exception:
            return (0, 0, 0)

    def compare_versions(self, v1: str, v2: str) -> int:
        """比较版本号: 1=v1>v2, -1=v1<v2, 0=相等"""
        p1 = self.parse_version(v1)
        p2 = self.parse_version(v2)
        if p1 > p2:
            return 1
        if p1 < p2:
            return -1
        return 0

    def check_for_updates(self, force: bool = False) -> tuple[bool, Optional[VersionInfo]]:
        """Check the update service and return (available, version_info)."""
        self._status = UpdateStatus.CHECKING
        self._progress = UpdateProgress(status=UpdateStatus.CHECKING, message="正在检查更新...")
        self._notify()
        # 尝试从更新服务器获取版本信息
        try:
            import urllib.request
            import urllib.error
            url = f"{self._update_url}?current={self._current_version}&platform={os.name}"
            req = urllib.request.Request(url, headers={"User-Agent": f"OfficeAgent/{self._current_version}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self._latest_version = VersionInfo(
                    version=data.get("version", ""),
                    release_date=data.get("release_date", ""),
                    release_notes=data.get("release_notes", ""),
                    download_url=data.get("download_url", ""),
                    file_size=data.get("file_size", 0),
                    checksum=data.get("checksum", ""),
                    is_mandatory=data.get("is_mandatory", False),
                    min_version=data.get("min_version", ""),
                )
        except Exception:
            # 网络不可用或服务器未配置，使用模拟检查
            self._latest_version = None
        # 比较版本
        if self._latest_version and self.compare_versions(self._latest_version.version, self._current_version) > 0:
            self._status = UpdateStatus.UPDATE_AVAILABLE
            self._progress = UpdateProgress(
                status=UpdateStatus.UPDATE_AVAILABLE,
                message=f"发现新版本 {self._latest_version.version}",
            )
            self._notify()
            return True, self._latest_version
        self._status = UpdateStatus.UP_TO_DATE
        self._progress = UpdateProgress(status=UpdateStatus.UP_TO_DATE, message="Already up to date")
        self._notify()
        return False, None

    def download_update(self, version_info: VersionInfo = None) -> bool:
        """Download an update package."""
        info = version_info or self._latest_version
        if not info or not info.download_url:
            self._progress = UpdateProgress(status=UpdateStatus.ERROR, error="No update is available")
            self._notify()
            return False
        self._status = UpdateStatus.DOWNLOADING
        self._progress = UpdateProgress(status=UpdateStatus.DOWNLOADING, message="正在下载更新...", total=info.file_size)
        self._notify()
        try:
            import urllib.request
            # 下载到临时目录
            self._download_path = Path(tempfile.gettempdir()) / f"OfficeAgent_{info.version}.update"
            def report_progress(block_num, block_size, total_size):
                downloaded = block_num * block_size
                self._progress.downloaded = downloaded
                self._progress.total = total_size or info.file_size
                self._progress.progress = (downloaded / total_size * 100) if total_size else 0
            urllib.request.urlretrieve(info.download_url, str(self._download_path), reporthook=report_progress)
            self._status = UpdateStatus.DOWNLOADED
            self._progress = UpdateProgress(status=UpdateStatus.DOWNLOADED, message="下载完成")
            self._notify()
            return True
        except Exception as e:
            self._status = UpdateStatus.ERROR
            self._progress = UpdateProgress(status=UpdateStatus.ERROR, error=str(e))
            self._notify()
            return False

    def install_update(self) -> bool:
        """Install the downloaded update package."""
        if not self._download_path or not self._download_path.exists():
            return False
        self._status = UpdateStatus.INSTALLING
        self._progress = UpdateProgress(status=UpdateStatus.INSTALLING, message="正在安装更新...")
        self._notify()
        # 记录更新历史
        if self._latest_version:
            self._history.append({
                "version": self._latest_version.version,
                "installed_at": time.time(),
                "previous_version": self._current_version,
            })
            self._save_history()
        # 实际安装逻辑：
        # 1. 校验checksum
        # 2. 备份当前版本
        # 3. 解压更新包
        # 4. 替换文件
        # 5. 重启应用
        # 这里只做框架
        self._status = UpdateStatus.IDLE
        return True

    def get_current_version(self) -> str:
        return self._current_version

    def get_latest_version(self) -> Optional[VersionInfo]:
        return self._latest_version

    def get_progress(self) -> UpdateProgress:
        return self._progress

    def get_update_history(self) -> list[dict]:
        return self._history

    def set_update_url(self, url: str) -> None:
        self._update_url = url

    def rollback(self, target_version: str = None) -> bool:
        """Roll back to a target version."""
        if not self._history:
            return False
        # 找到目标版本
        target = target_version
        if not target and len(self._history) >= 2:
            target = self._history[-2].get("version")
        if not target:
            return False
        self._status = UpdateStatus.INSTALLING
        self._progress = UpdateProgress(status=UpdateStatus.INSTALLING, message=f"正在回滚到 {target}...")
        self._notify()
        try:
            # 从备份恢复
            from office_agent.local.config.migration import ConfigMigrator
            migrator = ConfigMigrator()
            backups = migrator.list_backups()
            for backup in backups:
                if backup["version"] == target:
                    success = migrator.restore_from_backup(Path(backup["path"]))
                    if success:
                        self._history.append({
                            "version": target,
                            "rolled_back_at": time.time(),
                            "from_version": self._current_version,
                        })
                        self._save_history()
                        self._current_version = target
                    return success
            return False
        except Exception as e:
            self._progress = UpdateProgress(status=UpdateStatus.ERROR, error=f"回滚失败: {e}")
            self._notify()
            return False

    def verify_update(self, update_path: Path, expected_checksum: str) -> bool:
        """Verify an update package checksum."""
        import hashlib
        if not update_path.exists():
            return False
        sha = hashlib.sha256()
        with open(update_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                sha.update(chunk)
        return sha.hexdigest() == expected_checksum


# 全局实例
_update_manager: Optional[UpdateManager] = None


def get_update_manager() -> UpdateManager:
    global _update_manager
    if _update_manager is None:
        _update_manager = UpdateManager()
    return _update_manager
