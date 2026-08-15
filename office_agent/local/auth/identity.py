"""
Local Identity System - 本地用户身份系统
无需注册登录，默认本地用户
"""
import os
import json
import time
import uuid
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict


@dataclass
class LocalUserProfile:
    """本地用户配置文件"""
    user_id: str
    username: str
    display_name: str = ""
    avatar_path: str = ""
    settings: dict = field(default_factory=dict)
    preferences: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    is_local: bool = True
    cloud_sync_enabled: bool = False
    cloud_user_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LocalUserProfile":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class LocalIdentityManager:
    """本地身份管理器"""

    def __init__(self, data_dir: str = None):
        self._data_dir = Path(data_dir or self._default_data_dir())
        self._profile_path = self._data_dir / "user_profile.json"
        self._profile: Optional[LocalUserProfile] = None
        self._ensure_dirs()

    @staticmethod
    def _default_data_dir() -> str:
        """默认数据目录"""
        if os.name == "nt":  # Windows
            base = os.environ.get("APPDATA", os.path.expanduser("~"))
            return os.path.join(base, "OfficeAgent")
        elif os.name == "posix":
            if os.path.exists("/Applications"):  # macOS
                return os.path.expanduser("~/Library/Application Support/OfficeAgent")
            return os.path.expanduser("~/.local/share/OfficeAgent")
        return os.path.expanduser("~/.officeagent")

    def _ensure_dirs(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> LocalUserProfile:
        """初始化或加载本地用户"""
        if self._profile_path.exists():
            try:
                with open(self._profile_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._profile = LocalUserProfile.from_dict(data)
                self._profile.last_active_at = time.time()
                self._save()
                return self._profile
            except Exception:
                pass
        # 创建新用户
        self._profile = LocalUserProfile(
            user_id=f"local_{uuid.uuid4().hex[:12]}",
            username="local_user",
            display_name="本地用户",
            settings={
                "theme": "auto",
                "language": "zh-CN",
                "auto_save": True,
                "notifications": True,
            },
        )
        self._save()
        return self._profile

    def _save(self) -> None:
        if self._profile:
            with open(self._profile_path, "w", encoding="utf-8") as f:
                json.dump(self._profile.to_dict(), f, ensure_ascii=False, indent=2)

    def get_current_user(self) -> LocalUserProfile:
        """获取当前用户"""
        if self._profile is None:
            return self.initialize()
        return self._profile

    def update_profile(self, **kwargs) -> LocalUserProfile:
        """更新用户配置"""
        if self._profile is None:
            self.initialize()
        for key, value in kwargs.items():
            if hasattr(self._profile, key):
                setattr(self._profile, key, value)
        self._profile.last_active_at = time.time()
        self._save()
        return self._profile

    def update_settings(self, settings: dict) -> LocalUserProfile:
        """更新设置"""
        if self._profile is None:
            self.initialize()
        self._profile.settings.update(settings)
        self._save()
        return self._profile

    def get_setting(self, key: str, default=None):
        """获取设置"""
        if self._profile is None:
            self.initialize()
        return self._profile.settings.get(key, default)

    def get_data_dir(self) -> Path:
        return self._data_dir

    def reset(self) -> None:
        """重置（清除本地用户数据）"""
        if self._profile_path.exists():
            self._profile_path.unlink()
        self._profile = None
