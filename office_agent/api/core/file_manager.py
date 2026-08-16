"""
文件管理器 - 上传/下载/元数据管理
"""
import os
import uuid
import shutil
import mimetypes
import json
import threading
from typing import Dict, Optional, List, BinaryIO
from datetime import datetime

from ..core.config import settings
from ..core.exceptions import (
    FileError, FileTypeNotSupportedError,
)


class FileInfo:
    """文件信息"""
    def __init__(self, file_id: str, original_name: str, stored_path: str,
                 file_type: str, extension: str, size: int,
                 metadata: Dict = None):
        self.file_id = file_id
        self.original_name = original_name
        self.stored_path = stored_path
        self.file_type = file_type
        self.extension = extension
        self.size = size
        self.upload_time = datetime.now().isoformat()
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
        ".docx": "word", ".doc": "word",
        ".pptx": "ppt", ".ppt": "ppt",
        ".xlsx": "excel", ".xls": "excel",
        ".pdf": "pdf",
        ".txt": "text", ".md": "text",
        ".png": "image", ".jpg": "image", ".jpeg": "image",
        ".gif": "image", ".bmp": "image", ".webp": "image",
    }

    def __init__(self, upload_dir: str = None, output_dir: str = None):
        self.upload_dir = upload_dir or settings.upload_dir
        self.output_dir = output_dir or settings.output_dir
        os.makedirs(self.upload_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)
        self.files: Dict[str, FileInfo] = {}
        self._lock = threading.RLock()
        self._metadata_path = os.path.join(self.upload_dir, "file_metadata.json")
        self._load_metadata()

    def _load_metadata(self):
        try:
            with open(self._metadata_path, "r", encoding="utf-8") as f:
                for item in json.load(f):
                    info = FileInfo(item["file_id"], item.get("original_name", item.get("filename", "")),
                                    os.path.join(self.upload_dir, f"{item['file_id']}{item.get('extension', '')}"),
                                    item.get("file_type", "unknown"), item.get("extension", ""),
                                    int(item.get("size", 0)), item.get("metadata", {}))
                    info.upload_time = item.get("upload_time", info.upload_time)
                    if os.path.exists(info.stored_path):
                        self.files[info.file_id] = info
        except (FileNotFoundError, ValueError, OSError):
            return

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

        return {
            "upload_count": len([f for f in self.files.values() if "file_" in f.file_id]),
            "output_count": len([f for f in self.files.values() if "out_" in f.file_id]),
            "upload_size_bytes": upload_size,
            "output_size_bytes": output_size,
            "total_files": len(self.files),
        }


# 全局文件管理器
file_manager = FileManager()
