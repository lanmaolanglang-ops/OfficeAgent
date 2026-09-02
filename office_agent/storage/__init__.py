"""
Office Agent Storage Layer - 文件存储层

架构：
    Agent / API
        ↓
    StorageService（统一入口）
        ↓
    StorageBackend（抽象接口）
        ↓
    ┌────────────┐
    │ LocalStorage│
    └────────────┘

使用：
    from office_agent.storage import get_storage_service, FileInfo

    storage = get_storage_service()

    # 上传
    info = storage.upload("report.docx", content, owner_id="user_123")

    # 下载
    content, info = storage.download(file_id)

    # Agent 读取文件（获取本地路径）
    path = storage.get_file_path(file_id)

    # Agent 保存输出
    info = storage.save_output("/tmp/output.docx", original_name="report.docx",
                               parent_file_id=file_id, change_description="AI排版")

    # 版本管理
    versions = storage.get_versions(file_id)
    storage.restore_version(file_id, version_number=2)

    # 删除
    storage.delete(file_id)  # 软删除
    storage.delete(file_id, permanent=True)  # 物理删除
"""
from .storage_backend import StorageBackend
from .local_storage import LocalStorage
from .storage_service import (
    StorageService, StorageConfig, FileInfo,
    create_storage_backend, get_storage_service,
)
from .validators import (
    validate_file, validate_extension, validate_size,
    get_file_category, get_mime_type, compute_hash, compute_file_hash,
    FileValidationError, ALLOWED_EXTENSIONS,
)
from .path_generator import (
    generate_file_id, generate_storage_path, generate_temp_path,
    BUCKET_UPLOADS, BUCKET_OUTPUTS, BUCKET_TEMP, BUCKET_CACHE, BUCKET_VERSIONS,
)
from .._version import __version__

__all__ = [
    "__version__",
    "StorageBackend", "LocalStorage",
    "StorageService", "StorageConfig", "FileInfo",
    "create_storage_backend", "get_storage_service",
    "validate_file", "validate_extension", "validate_size",
    "get_file_category", "get_mime_type", "compute_hash", "compute_file_hash",
    "FileValidationError", "ALLOWED_EXTENSIONS",
    "generate_file_id", "generate_storage_path", "generate_temp_path",
    "BUCKET_UPLOADS", "BUCKET_OUTPUTS", "BUCKET_TEMP", "BUCKET_CACHE",
    "BUCKET_VERSIONS",
]
