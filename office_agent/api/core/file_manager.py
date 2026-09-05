"""
文件管理器 - 上传/下载/元数据管理
"""
import os
import json
import logging
import threading
from typing import Dict
from datetime import datetime, timezone

from ..core.config import settings

logger = logging.getLogger(__name__)


class FileInfo:
    """文件信息"""
    def __init__(self, file_id: str, original_name: str, stored_path: str,
                 file_type: str, extension: str, size: int,
                 metadata: Dict | None = None):
        self.file_id = file_id
        self.original_name = original_name
        self.stored_path = stored_path
        self.file_type = file_type
        self.extension = extension
        self.size = size
        self.upload_time = datetime.now(timezone.utc).isoformat()
        self.metadata = metadata or {}

    def to_dict(self) -> Dict:
        return {
            "file_id": self.file_id,
            "filename": self.original_name,
            "original_name": self.original_name,
            "file_type": self.file_type,
            "extension": self.extension,
            "size": self.size,
            "upload_time": self.upload_time,
            "metadata": self.metadata,
        }


class FileManager:
    """文件管理器"""

    # 文件类型映射
    TYPE_MAP = {
        ".docx": "word",
        ".pptx": "ppt",
        ".xlsx": "excel",
        ".pdf": "pdf",
        ".txt": "text", ".md": "text",
        ".png": "image", ".jpg": "image", ".jpeg": "image",
        ".gif": "image", ".bmp": "image", ".webp": "image",
    }

    def __init__(self, upload_dir: str | None = None, output_dir: str | None = None):
        self.upload_dir = upload_dir or settings.upload_dir
        self.output_dir = output_dir or settings.output_dir
        os.makedirs(self.upload_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        self.files: Dict[str, FileInfo] = {}
        self._lock = threading.RLock()
        self._metadata_path = os.path.join(self.upload_dir, "file_metadata.json")
        self._load_metadata()

    def _load_metadata(self):
        with self._lock:
            try:
                with open(self._metadata_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                if not isinstance(payload, list):
                    logger.warning("忽略无效文件元数据根节点（应为数组）: %s", self._metadata_path)
                    return
                for item in payload:
                    if not isinstance(item, dict) or not item.get("file_id"):
                        logger.warning("忽略无效文件元数据条目")
                        continue
                    info = FileInfo(item["file_id"], item.get("original_name", item.get("filename", "")),
                                    item.get("stored_path") or os.path.join(
                                        self.upload_dir,
                                        f"{item['file_id']}{item.get('extension', '')}",
                                    ),
                                    item.get("file_type", "unknown"), item.get("extension", ""),
                                    int(item.get("size", 0)), item.get("metadata", {}))
                    info.upload_time = item.get("upload_time", info.upload_time)
                    if os.path.exists(info.stored_path):
                        self.files[info.file_id] = info
            except FileNotFoundError:
                return
            except (ValueError, TypeError, KeyError, OSError) as exc:
                logger.warning("加载文件元数据失败，保留空索引: %s", exc)

    def save_metadata(self) -> None:
        """在锁内用 fsync + replace 原子持久化当前元数据索引。"""
        with self._lock:
            payload = []
            for info in self.files.values():
                item = info.to_dict()
                item["stored_path"] = info.stored_path
                payload.append(item)
            # 统一原子写工具：同目录临时文件 + fsync + replace
            from ...persistence import atomic_write_json

            atomic_write_json(self._metadata_path, payload, indent=2)

    def register(self, info: FileInfo) -> FileInfo:
        """注册文件并可靠落盘。"""
        with self._lock:
            self.files[info.file_id] = info
            try:
                self.save_metadata()
            except Exception:
                self.files.pop(info.file_id, None)
                raise
        return info

    def unregister(self, file_id: str) -> bool:
        """仅移除元数据记录；实体文件生命周期由存储服务负责。"""
        with self._lock:
            previous = self.files.pop(file_id, None)
            if previous is None:
                return False
            try:
                self.save_metadata()
            except Exception:
                self.files[file_id] = previous
                raise
            return True

    def get_storage_stats(self) -> Dict:
        """存储统计"""
        upload_size = sum(
            os.path.getsize(os.path.join(self.upload_dir, f))
            for f in os.listdir(self.upload_dir)
            if os.path.isfile(os.path.join(self.upload_dir, f))
        ) if os.path.exists(self.upload_dir) else 0

        output_size = sum(
            os.path.getsize(os.path.join(self.output_dir, f))
            for f in os.listdir(self.output_dir)
            if os.path.isfile(os.path.join(self.output_dir, f))
        ) if os.path.exists(self.output_dir) else 0

        with self._lock:
            files = list(self.files.values())
        return {
            "upload_count": len([f for f in files if "file_" in f.file_id]),
            "output_count": len([f for f in files if "out_" in f.file_id]),
            "upload_size_bytes": upload_size,
            "output_size_bytes": output_size,
            "total_files": len(files),
        }


class _LazyFileManager:
    """Compatibility proxy that avoids filesystem writes during module import."""

    def __init__(self):
        self._instance = None
        self._instance_lock = threading.Lock()

    def _get(self) -> FileManager:
        if self._instance is None:
            with self._instance_lock:
                if self._instance is None:
                    self._instance = FileManager()
        return self._instance

    def __getattr__(self, name):
        return getattr(self._get(), name)


file_manager = _LazyFileManager()
