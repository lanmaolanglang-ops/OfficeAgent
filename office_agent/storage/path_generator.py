"""
文件路径生成器

规则：
- 每个文件有唯一 file_id（UUID）
- 按日期分目录存储，避免单目录文件过多
- 按用途分目录：uploads / outputs / temp / cache

目录结构：
    storage_root/
    ├── uploads/
    │   └── 2026/07/31/
    │       └── {file_id}{ext}
    ├── outputs/
    │   └── 2026/07/31/
    │       └── {file_id}{ext}
    ├── temp/
    ├── cache/
    └── versions/
        └── {parent_file_id}/
            └── v{version}_{file_id}{ext}
"""
import os
import uuid
from datetime import datetime
from typing import Optional
from pathlib import Path


# 存储桶/目录类型
BUCKET_UPLOADS = "uploads"
BUCKET_OUTPUTS = "outputs"
BUCKET_TEMP = "temp"
BUCKET_CACHE = "cache"
BUCKET_VERSIONS = "versions"

ALL_BUCKETS = [BUCKET_UPLOADS, BUCKET_OUTPUTS, BUCKET_TEMP, BUCKET_CACHE, BUCKET_VERSIONS]


def generate_file_id() -> str:
    """生成唯一文件ID"""
    return f"file_{uuid.uuid4().hex[:12]}"


def generate_version_id() -> str:
    """生成版本ID"""
    return f"ver_{uuid.uuid4().hex[:12]}"


def _date_path() -> str:
    """生成日期路径：YYYY/MM/DD"""
    now = datetime.now()
    return os.path.join(f"{now.year:04d}", f"{now.month:02d}", f"{now.day:02d}")


def generate_storage_path(bucket: str, file_id: str, extension: str,
                          parent_file_id: str = None) -> str:
    """
    生成存储路径

    Args:
        bucket: 存储桶类型 (uploads/outputs/temp/cache/versions)
        file_id: 文件ID
        extension: 文件扩展名（含点，如 .docx）
        parent_file_id: 父文件ID（版本文件用）

    Returns:
        相对存储路径
    """
    ext = extension if extension.startswith(".") else f".{extension}"

    if bucket == BUCKET_VERSIONS and parent_file_id:
        # 版本文件：versions/{parent_id}/v{n}_{file_id}{ext}
        return os.path.join(bucket, parent_file_id, f"{file_id}{ext}")

    if bucket in (BUCKET_TEMP, BUCKET_CACHE):
        # 临时/缓存文件不分日期
        return os.path.join(bucket, f"{file_id}{ext}")

    # 普通文件按日期分目录
    return os.path.join(bucket, _date_path(), f"{file_id}{ext}")


def generate_temp_path(prefix: str = "tmp", extension: str = ".tmp") -> str:
    """生成临时文件路径"""
    tmp_id = uuid.uuid4().hex[:8]
    ext = extension if extension.startswith(".") else f".{extension}"
    return os.path.join(BUCKET_TEMP, f"{prefix}_{tmp_id}{ext}")


def parse_storage_path(storage_path: str) -> dict:
    """
    解析存储路径，提取元信息

    Returns:
        {"bucket": ..., "file_id": ..., "extension": ...}
    """
    parts = Path(storage_path).parts
    if not parts:
        return {}

    bucket = parts[0]
    filename = parts[-1]

    # 从文件名提取 file_id 和 ext
    name, ext = os.path.splitext(filename)
    return {
        "bucket": bucket,
        "file_id": name,
        "extension": ext,
    }
