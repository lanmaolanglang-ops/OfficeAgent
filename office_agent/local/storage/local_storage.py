"""
Local File Storage - 本地文件存储
所有文件默认保存在本地，支持用户自定义路径
"""
import os
import json
import shutil
import hashlib
import mimetypes
from pathlib import Path
from typing import Optional, BinaryIO
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FileInfo:
    """文件信息"""
    file_id: str
    filename: str
    path: str
    size: int
    mime_type: str
    category: str  # documents/outputs/templates/cache
    created_at: float
    modified_at: float
    checksum: str = ""
    metadata: dict = None

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "path": self.path,
            "size": self.size,
            "mime_type": self.mime_type,
            "category": self.category,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "checksum": self.checksum,
            "metadata": self.metadata or {},
        }


class LocalFileStorage:
    """
    本地文件存储
    目录结构：
        OfficeAgent/
        ├── data/
        │   ├── documents/    # 用户上传的文档
        │   ├── outputs/      # 处理结果
        │   ├── templates/    # 模板文件
        │   ├── cache/        # 缓存文件
        │   ├── temp/         # 临时文件
        │   └── exports/      # 导出文件
        ├── database/         # SQLite数据库
        ├── logs/             # 日志
        └── config/           # 配置
    """

    DEFAULT_DIRS = {
        "documents": "data/documents",
        "outputs": "data/outputs",
        "templates": "data/templates",
        "cache": "data/cache",
        "temp": "data/temp",
        "exports": "data/exports",
        "database": "database",
        "logs": "logs",
        "config": "config",
    }

    def __init__(self, base_dir: str = None):
        self._base_dir = Path(base_dir or self._default_base_dir())
        self._dirs: dict[str, Path] = {}
        self._index_path = self._base_dir / "file_index.json"
        self._file_index: dict[str, FileInfo] = {}
        self._ensure_dirs()
        self._load_index()

    @staticmethod
    def _default_base_dir() -> str:
        if os.name == "nt":
            base = os.environ.get("APPDATA", os.path.expanduser("~"))
            return os.path.join(base, "OfficeAgent")
        elif os.path.exists("/Applications"):
            return os.path.expanduser("~/Library/Application Support/OfficeAgent")
        return os.path.expanduser("~/.local/share/OfficeAgent")

    def _ensure_dirs(self) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        for name, rel_path in self.DEFAULT_DIRS.items():
            dir_path = self._base_dir / rel_path
            dir_path.mkdir(parents=True, exist_ok=True)
            self._dirs[name] = dir_path

    def _load_index(self) -> None:
        if self._index_path.exists():
            try:
                with open(self._index_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for file_id, info in data.items():
                    self._file_index[file_id] = FileInfo(**info)
            except Exception:
                self._file_index = {}

    def _save_index(self) -> None:
        data = {fid: info.to_dict() for fid, info in self._file_index.items()}
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def get_dir(self, category: str) -> Path:
        return self._dirs.get(category, self._dirs["documents"])

    def set_custom_base_dir(self, new_dir: str) -> None:
        """用户自定义存储路径"""
        old_base = self._base_dir
        self._base_dir = Path(new_dir)
        self._ensure_dirs()
        # 迁移文件（可选）
        if old_base.exists() and old_base != self._base_dir:
            for name in self.DEFAULT_DIRS:
                old_dir = old_base / self.DEFAULT_DIRS[name]
                new_dir = self._dirs[name]
                if old_dir.exists():
                    for item in old_dir.iterdir():
                        if item.is_file():
                            shutil.copy2(str(item), str(new_dir / item.name))

    def save_file(
        self,
        content: bytes | BinaryIO,
        filename: str,
        category: str = "documents",
        file_id: str = None,
        metadata: dict = None,
    ) -> FileInfo:
        """保存文件"""
        import uuid
        file_id = file_id or f"file_{uuid.uuid4().hex[:12]}"
        target_dir = self.get_dir(category)
        # 处理重名
        safe_name = self._safe_filename(filename)
        target_path = target_dir / safe_name
        if target_path.exists():
            stem = target_path.stem
            suffix = target_path.suffix
            counter = 1
            while target_path.exists():
                target_path = target_dir / f"{stem}_{counter}{suffix}"
                counter += 1
        # 写入内容
        if isinstance(content, bytes):
            with open(target_path, "wb") as f:
                f.write(content)
        else:
            with open(target_path, "wb") as f:
                shutil.copyfileobj(content, f)
        # 文件信息
        stat = target_path.stat()
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        checksum = self._calc_checksum(target_path)
        info = FileInfo(
            file_id=file_id,
            filename=target_path.name,
            path=str(target_path),
            size=stat.st_size,
            mime_type=mime_type,
            category=category,
            created_at=stat.st_ctime,
            modified_at=stat.st_mtime,
            checksum=checksum,
            metadata=metadata or {},
        )
        self._file_index[file_id] = info
        self._save_index()
        return info

    def save_file_from_path(self, source_path: str, category: str = "documents", **kwargs) -> FileInfo:
        """从已有路径复制文件"""
        with open(source_path, "rb") as f:
            filename = kwargs.pop("filename", os.path.basename(source_path))
            return self.save_file(f, filename, category, **kwargs)

    def get_file(self, file_id: str) -> Optional[FileInfo]:
        return self._file_index.get(file_id)

    def get_file_path(self, file_id: str) -> Optional[str]:
        info = self._file_index.get(file_id)
        return info.path if info else None

    def read_file(self, file_id: str) -> Optional[bytes]:
        info = self._file_index.get(file_id)
        if info and os.path.exists(info.path):
            with open(info.path, "rb") as f:
                return f.read()
        return None

    def delete_file(self, file_id: str, delete_from_disk: bool = True) -> bool:
        info = self._file_index.pop(file_id, None)
        if info:
            if delete_from_disk and os.path.exists(info.path):
                try:
                    os.remove(info.path)
                except OSError:
                    pass
            self._save_index()
            return True
        return False

    def list_files(self, category: str = None) -> list[FileInfo]:
        files = list(self._file_index.values())
        if category:
            files = [f for f in files if f.category == category]
        return sorted(files, key=lambda x: x.created_at, reverse=True)

    def cleanup_temp(self, max_age_hours: int = 24) -> int:
        """清理临时文件"""
        import time
        now = time.time()
        count = 0
        temp_dir = self._dirs["temp"]
        cache_dir = self._dirs["cache"]
        for dir_path in [temp_dir, cache_dir]:
            if dir_path.exists():
                for item in dir_path.iterdir():
                    if item.is_file():
                        age = now - item.stat().st_mtime
                        if age > max_age_hours * 3600:
                            try:
                                item.unlink()
                                count += 1
                            except OSError:
                                pass
        return count

    def get_storage_stats(self) -> dict:
        """获取存储统计"""
        stats = {}
        total_size = 0
        total_files = 0
        for name, dir_path in self._dirs.items():
            size = 0
            count = 0
            if dir_path.exists():
                for item in dir_path.rglob("*"):
                    if item.is_file():
                        size += item.stat().st_size
                        count += 1
            stats[name] = {"size_bytes": size, "file_count": count}
            total_size += size
            total_files += count
        stats["total"] = {"size_bytes": total_size, "file_count": total_files}
        # 磁盘空间
        try:
            disk = shutil.disk_usage(str(self._base_dir))
            stats["disk"] = {
                "total_gb": round(disk.total / (1024**3), 2),
                "used_gb": round(disk.used / (1024**3), 2),
                "free_gb": round(disk.free / (1024**3), 2),
            }
        except Exception:
            pass
        return stats

    @staticmethod
    def _safe_filename(filename: str) -> str:
        safe = "".join(c for c in filename if c.isalnum() or c in "._- ()[]{}中文")
        return safe[:200] or "unnamed"

    @staticmethod
    def _calc_checksum(path: Path) -> str:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()[:16]
        except Exception:
            return ""


# 全局实例
_storage: Optional[LocalFileStorage] = None


def get_storage(base_dir: str = None) -> LocalFileStorage:
    global _storage
    if _storage is None:
        _storage = LocalFileStorage(base_dir)
    return _storage
