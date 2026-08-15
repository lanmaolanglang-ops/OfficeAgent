"""
文件校验器

负责：
- 文件大小限制
- 文件类型白名单
- 扩展名检查
- MIME 类型检查
- 文件内容嗅探（防止扩展名伪造）
"""
import os
import hashlib
from typing import Tuple, Optional

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {
    # Office
    ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    # PDF
    ".pdf",
    # 文本
    ".txt", ".md", ".csv", ".json", ".xml", ".html",
    # 图片
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
}

# 危险扩展名（直接拒绝）
DANGEROUS_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".psm1",
    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta",
    ".dll", ".so", ".dylib", ".sh", ".bash", ".zsh",
    ".reg", ".inf", ".lnk",
}

# MIME 类型映射
MIME_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xml": "application/xml",
    ".html": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

# 文件类型分类
FILE_CATEGORIES = {
    ".docx": "word", ".doc": "word",
    ".pptx": "ppt", ".ppt": "ppt",
    ".xlsx": "excel", ".xls": "excel",
    ".pdf": "pdf",
    ".txt": "text", ".md": "text", ".csv": "text", ".json": "text", ".xml": "text", ".html": "text",
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".gif": "image", ".bmp": "image", ".webp": "image", ".svg": "image",
}

# 默认大小限制（字节）
DEFAULT_MAX_SIZE = 100 * 1024 * 1024  # 100MB
CHUNK_SIZE = 10 * 1024 * 1024  # 10MB 分片


class FileValidationError(Exception):
    """文件校验错误"""
    pass


def get_extension(filename: str) -> str:
    """获取小写扩展名"""
    return os.path.splitext(filename)[1].lower()


def get_file_category(filename: str) -> str:
    """获取文件分类"""
    ext = get_extension(filename)
    return FILE_CATEGORIES.get(ext, "unknown")


def get_mime_type(filename: str) -> str:
    """获取 MIME 类型"""
    ext = get_extension(filename)
    return MIME_TYPES.get(ext, "application/octet-stream")


def validate_extension(filename: str) -> str:
    """校验扩展名，返回扩展名或抛出异常"""
    ext = get_extension(filename)

    if ext in DANGEROUS_EXTENSIONS:
        raise FileValidationError(f"危险文件类型不允许上传: {ext}")

    if ext not in ALLOWED_EXTENSIONS:
        raise FileValidationError(
            f"不支持的文件类型: {ext}，支持: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    return ext


def validate_size(content: bytes, max_size: int = DEFAULT_MAX_SIZE) -> int:
    """校验文件大小"""
    size = len(content)
    if size > max_size:
        raise FileValidationError(
            f"文件大小 {size / 1024 / 1024:.1f}MB 超过限制 {max_size / 1024 / 1024}MB"
        )
    if size == 0:
        raise FileValidationError("文件内容为空")
    return size


def compute_hash(content: bytes, algorithm: str = "sha256") -> str:
    """计算文件哈希"""
    h = hashlib.new(algorithm)
    h.update(content)
    return h.hexdigest()


def compute_file_hash(file_path: str, algorithm: str = "sha256") -> str:
    """计算文件哈希（流式）"""
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def validate_file(filename: str, content: bytes,
                  max_size: int = DEFAULT_MAX_SIZE) -> dict:
    """
    完整文件校验

    Returns:
        {
            "original_name": 原始文件名,
            "extension": 扩展名,
            "file_type": 文件分类(word/ppt/excel/...),
            "mime_type": MIME类型,
            "size": 文件大小,
            "hash": SHA256哈希,
        }
    """
    ext = validate_extension(filename)
    size = validate_size(content, max_size)
    file_hash = compute_hash(content)

    return {
        "original_name": filename,
        "extension": ext,
        "file_type": get_file_category(filename),
        "mime_type": get_mime_type(filename),
        "size": size,
        "hash": file_hash,
    }
