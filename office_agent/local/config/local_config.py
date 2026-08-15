"""
Local Config Manager - 本地配置管理器
管理config.json：模型配置、文件路径、主题、Agent默认设置
"""
import os
import json
import time
from pathlib import Path
from typing import Any, Optional
from copy import deepcopy


DEFAULT_CONFIG = {
    "version": "0.49.0",
    "app": {
        "name": "OfficeAgent",
        "language": "zh-CN",
        "theme": "auto",  # auto/light/dark
        "auto_start": False,
        "minimize_to_tray": True,
        "check_updates": True,
    },
    "paths": {
        "data_dir": "",  # 空表示默认
        "documents_dir": "",
        "outputs_dir": "",
        "templates_dir": "",
        "custom_paths": {},
    },
    "models": {
        "default_provider": "",
        "default_model": "",
        "fallback_enabled": True,
        "fallback_chain": [],
        "max_tokens": 4096,
        "temperature": 0.7,
        "timeout": 60,
    },
    "agents": {
        "word": {
            "enabled": True,
            "default_style": "business",
            "auto_format": True,
            "quality_check": True,
        },
        "ppt": {
            "enabled": True,
            "default_template": "",
            "auto_design": True,
            "speaker_notes": True,
        },
        "excel": {
            "enabled": True,
            "auto_analyze": True,
            "auto_chart": True,
            "decimal_places": 2,
        },
    },
    "runtime": {
        "host": "127.0.0.1",
        "port": 8765,
        "auto_start_backend": True,
        "max_workers": 4,
        "task_timeout": 300,
        "auto_restart": True,
    },
    "security": {
        "sandbox_enabled": True,
        "prompt_scan": True,
        "file_scan": True,
        "audit_log": True,
        "allow_network": True,
    },
    "storage": {
        "max_cache_size_mb": 500,
        "auto_cleanup_temp": True,
        "temp_retention_hours": 24,
        "auto_backup": False,
        "backup_dir": "",
    },
    "ui": {
        "show_welcome": True,
        "recent_files_count": 20,
        "notification_sound": True,
        "compact_mode": False,
    },
}


class LocalConfigManager:
    """本地配置管理器"""

    def __init__(self, config_dir: str = None):
        self._config_dir = Path(config_dir or self._default_dir())
        self._config_path = self._config_dir / "config.json"
        self._config: dict = {}
        self._watchers: list = []
        self._load()

    @staticmethod
    def _default_dir() -> str:
        if os.name == "nt":
            return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OfficeAgent", "config")
        elif os.path.exists("/Applications"):
            return os.path.expanduser("~/Library/Application Support/OfficeAgent/config")
        return os.path.expanduser("~/.local/share/OfficeAgent/config")

    def _load(self) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._config = self._merge_config(deepcopy(DEFAULT_CONFIG), loaded)
            except Exception:
                self._config = deepcopy(DEFAULT_CONFIG)
                self._save()
        else:
            self._config = deepcopy(DEFAULT_CONFIG)
            self._save()

    def _merge_config(self, default: dict, override: dict) -> dict:
        """递归合并配置"""
        result = deepcopy(default)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_config(result[key], value)
            else:
                result[key] = value
        return result

    def _save(self) -> None:
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, ensure_ascii=False, indent=2)

    def get(self, key_path: str = None, default: Any = None) -> Any:
        """
        获取配置值，支持点分路径
        例如: get("models.default_model")
        """
        if key_path is None:
            return deepcopy(self._config)
        keys = key_path.split(".")
        value = self._config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return deepcopy(value)

    def set(self, key_path: str, value: Any, save: bool = True) -> None:
        """设置配置值"""
        keys = key_path.split(".")
        config = self._config
        for key in keys[:-1]:
            if key not in config or not isinstance(config[key], dict):
                config[key] = {}
            config = config[key]
        config[keys[-1]] = value
        if save:
            self._save()

    def update(self, updates: dict, save: bool = True) -> None:
        """批量更新配置"""
        for key_path, value in updates.items():
            self.set(key_path, value, save=False)
        if save:
            self._save()

    def reset(self, key_path: str = None) -> None:
        """重置配置"""
        if key_path is None:
            self._config = deepcopy(DEFAULT_CONFIG)
        else:
            keys = key_path.split(".")
            default_value = DEFAULT_CONFIG
            for key in keys:
                if isinstance(default_value, dict) and key in default_value:
                    default_value = default_value[key]
                else:
                    return
            self.set(key_path, default_value)
        self._save()

    def get_data_dir(self) -> Path:
        """获取数据目录"""
        custom = self.get("paths.data_dir")
        if custom:
            return Path(custom)
        if os.name == "nt":
            return Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "OfficeAgent"
        elif os.path.exists("/Applications"):
            return Path.home() / "Library" / "Application Support" / "OfficeAgent"
        return Path.home() / ".local" / "share" / "OfficeAgent"

    def get_model_config(self) -> dict:
        """获取模型配置"""
        return self.get("models", {})

    def get_runtime_config(self) -> dict:
        """获取运行时配置"""
        return self.get("runtime", {})

    def get_agent_config(self, agent_type: str) -> dict:
        """获取Agent配置"""
        return self.get(f"agents.{agent_type}", {})

    def export_config(self, path: str) -> None:
        """导出配置"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, ensure_ascii=False, indent=2)

    def import_config(self, path: str) -> bool:
        """导入配置"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self._config = self._merge_config(deepcopy(DEFAULT_CONFIG), loaded)
            self._save()
            return True
        except Exception:
            return False

    @property
    def config_path(self) -> Path:
        return self._config_path


# 全局实例
_config: Optional[LocalConfigManager] = None


def get_config() -> LocalConfigManager:
    global _config
    if _config is None:
        _config = LocalConfigManager()
    return _config
