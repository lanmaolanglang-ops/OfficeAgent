"""
Config Migration - 配置迁移工具
升级时保留用户配置、API Key、模型配置、历史记录
"""
import os
import json
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger("office_agent.migration")


@dataclass
class MigrationResult:
    from_version: str
    to_version: str
    success: bool
    migrated_files: list[str] = field(default_factory=list)
    backed_up_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    backup_dir: str = ""


class ConfigMigrator:
    """配置迁移器"""

    # 需要迁移的用户数据
    USER_DATA_FILES = [
        "config/config.json",
        "config/model_configs.json",
        "config/user_profile.json",
        "config/preferences.json",
        "credentials.enc",
        "credentials.salt",
        "database/officeagent.db",
        "database/officeagent.db-wal",
        "database/officeagent.db-shm",
        "file_index.json",
    ]

    # 需要迁移的目录
    USER_DATA_DIRS = [
        "documents",
        "outputs",
        "templates",
        "exports",
    ]

    def __init__(self, data_dir: Path = None, backup_dir: Path = None):
        if data_dir is None:
            if os.name == "nt":
                data_dir = Path(os.environ.get("APPDATA", Path.home())) / "OfficeAgent"
            else:
                data_dir = Path.home() / ".officeagent"
        self.data_dir = data_dir
        self.backup_dir = backup_dir or data_dir / "backups"

    def backup_user_data(self, version: str = None) -> Path:
        """备份用户数据"""
        version = version or datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"backup_{version}"
        backup_path.mkdir(parents=True, exist_ok=True)
        backed_up = []
        # 备份文件
        for rel_path in self.USER_DATA_FILES:
            src = self.data_dir / rel_path
            if src.exists():
                dst = backup_path / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src, dst)
                    backed_up.append(rel_path)
                except Exception as e:
                    logger.error(f"Backup failed for {rel_path}: {e}")
        # 备份目录
        for rel_dir in self.USER_DATA_DIRS:
            src = self.data_dir / rel_dir
            if src.exists() and src.is_dir():
                dst = backup_path / rel_dir
                try:
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                    backed_up.append(rel_dir + "/")
                except Exception as e:
                    logger.error(f"Backup dir failed for {rel_dir}: {e}")
        # 备份清单
        manifest = {
            "version": version,
            "timestamp": datetime.now().isoformat(),
            "files": backed_up,
            "data_dir": str(self.data_dir),
        }
        (backup_path / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Backed up {len(backed_up)} items to {backup_path}")
        return backup_path

    def migrate(self, from_version: str, to_version: str) -> MigrationResult:
        """执行版本迁移"""
        result = MigrationResult(
            from_version=from_version,
            to_version=to_version,
            success=True,
        )
        logger.info(f"Migrating from {from_version} to {to_version}")
        # 1. 先备份
        try:
            backup_path = self.backup_user_data(to_version)
            result.backup_dir = str(backup_path)
            result.backed_up_files = [f.name for f in backup_path.iterdir() if f.is_file()]
        except Exception as e:
            result.errors.append(f"Backup failed: {e}")
            result.success = False
            return result
        # 2. 执行迁移脚本
        migrations = self._get_migrations(from_version, to_version)
        for migrate_fn in migrations:
            try:
                migrate_fn()
            except Exception as e:
                result.errors.append(f"Migration {migrate_fn.__name__} failed: {e}")
                result.success = False
        # 3. 更新版本标记
        try:
            version_file = self.data_dir / "config" / "version.json"
            version_file.parent.mkdir(parents=True, exist_ok=True)
            version_data = {
                "version": to_version,
                "migrated_from": from_version,
                "migrated_at": datetime.now().isoformat(),
            }
            version_file.write_text(json.dumps(version_data, indent=2), encoding="utf-8")
            result.migrated_files.append("config/version.json")
        except Exception as e:
            result.errors.append(f"Version update failed: {e}")
        return result

    def _get_migrations(self, from_ver: str, to_ver: str) -> list:
        """获取需要执行的迁移函数"""
        migrations = []
        # v0.47 -> v0.48: 添加新配置项
        if self._version_less(from_ver, "0.48.0") and not self._version_less(to_ver, "0.48.0"):
            migrations.append(self._migrate_to_048)
        return migrations

    def _migrate_to_048(self):
        """迁移到v0.48"""
        config_file = self.data_dir / "config" / "config.json"
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                # 添加新默认配置
                if "runtime" not in config:
                    config["runtime"] = {}
                config["runtime"].setdefault("port", 8765)
                config["runtime"].setdefault("auto_restart", True)
                config["runtime"].setdefault("health_check_interval", 5.0)
                if "local" not in config:
                    config["local"] = {"mode": "local", "first_run": False}
                with open(config_file, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Config migration failed: {e}")

    def restore_from_backup(self, backup_path: Path) -> bool:
        """从备份恢复"""
        manifest_file = backup_path / "manifest.json"
        if not manifest_file.exists():
            logger.error(f"No manifest in {backup_path}")
            return False
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            for rel_path in manifest.get("files", []):
                src = backup_path / rel_path
                dst = self.data_dir / rel_path
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            logger.info(f"Restored from {backup_path}")
            return True
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return False

    def list_backups(self) -> list[dict]:
        """列出所有备份"""
        backups = []
        if not self.backup_dir.exists():
            return backups
        for backup_path in sorted(self.backup_dir.iterdir(), reverse=True):
            manifest_file = backup_path / "manifest.json"
            if manifest_file.exists():
                try:
                    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                    backups.append({
                        "path": str(backup_path),
                        "version": manifest.get("version", "unknown"),
                        "timestamp": manifest.get("timestamp", ""),
                        "file_count": len(manifest.get("files", [])),
                    })
                except Exception:
                    pass
        return backups

    def cleanup_old_backups(self, keep_count: int = 5) -> int:
        """清理旧备份"""
        backups = self.list_backups()
        removed = 0
        for backup in backups[keep_count:]:
            try:
                shutil.rmtree(backup["path"])
                removed += 1
            except Exception:
                pass
        return removed

    @staticmethod
    def _version_less(v1: str, v2: str) -> bool:
        """比较版本号 v1 < v2"""
        def parse(v):
            return tuple(int(x) for x in v.split(".")[:3])
        try:
            return parse(v1) < parse(v2)
        except Exception:
            return False
