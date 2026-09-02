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
import io
import json
import zipfile
from typing import BinaryIO
from ..runtime_config import ALLOWED_UPLOAD_EXTENSIONS

# 允许的文件扩展名
ALLOWED_EXTENSIONS = ALLOWED_UPLOAD_EXTENSIONS

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
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xml": "application/xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}

# 文件类型分类
FILE_CATEGORIES = {
    ".docx": "word",
    ".pptx": "ppt",
    ".xlsx": "excel",
    ".pdf": "pdf",
    ".txt": "text", ".md": "text", ".csv": "text", ".json": "text", ".xml": "text",
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".gif": "image", ".bmp": "image", ".webp": "image",
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


_BINARY_SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".bmp": (b"BM",),
    ".webp": (b"RIFF",),
}

_OFFICE_REQUIRED_PREFIX = {
    ".docx": "word/",
    ".pptx": "ppt/",
    ".xlsx": "xl/",
}


def _validate_zip_office(ext: str, stream: BinaryIO) -> None:
    """Validate OOXML as a ZIP package with the expected application tree."""
    position = stream.tell()
    try:
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            names = archive.namelist()
            if "[Content_Types].xml" not in names:
                raise FileValidationError("Office 文件缺少 [Content_Types].xml")
            prefix = _OFFICE_REQUIRED_PREFIX[ext]
            if not any(name.startswith(prefix) for name in names):
                raise FileValidationError(f"文件内容与扩展名 {ext} 不匹配")
            bad_member = archive.testzip()
            if bad_member:
                raise FileValidationError("Office 文件压缩包已损坏")
    except (zipfile.BadZipFile, OSError) as exc:
        raise FileValidationError(f"文件内容与扩展名 {ext} 不匹配") from exc
    finally:
        stream.seek(position)


def _validate_text_content(ext: str, sample: bytes, stream: BinaryIO) -> None:
    if b"\x00" in sample:
        raise FileValidationError(f"文件内容与文本扩展名 {ext} 不匹配")
    if sample.startswith((b"MZ", b"\x7fELF")):
        raise FileValidationError("检测到可执行文件内容，已拒绝上传")
    if ext == ".json":
        position = stream.tell()
        try:
            stream.seek(0)
            raw = stream.read()
            json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FileValidationError("JSON 文件内容无效") from exc
        finally:
            stream.seek(position)


def validate_fileobj(filename: str, fileobj: BinaryIO,
                     max_size: int = DEFAULT_MAX_SIZE) -> dict:
    """Validate a seekable stream without materializing the whole file in RAM."""
    ext = validate_extension(filename)
    try:
        fileobj.seek(0)
    except (AttributeError, OSError) as exc:
        raise FileValidationError("上传流不可读取") from exc

    digest = hashlib.sha256()
    size = 0
    sample = b""
    while True:
        chunk = fileobj.read(1024 * 1024)
        if not chunk:
            break
        if not sample:
            sample = chunk[:8192]
        size += len(chunk)
        if size > max_size:
            fileobj.seek(0)
            raise FileValidationError(
                f"文件大小超过限制 {max_size / 1024 / 1024:.0f}MB"
            )
        digest.update(chunk)
    fileobj.seek(0)
    if size == 0:
        raise FileValidationError("文件内容为空")

    if ext in _OFFICE_REQUIRED_PREFIX:
        _validate_zip_office(ext, fileobj)
    elif ext in _BINARY_SIGNATURES:
        signatures = _BINARY_SIGNATURES[ext]
        if not any(sample.startswith(signature) for signature in signatures):
            raise FileValidationError(f"文件内容与扩展名 {ext} 不匹配")
        if ext == ".webp" and sample[8:12] != b"WEBP":
            raise FileValidationError("文件内容与扩展名 .webp 不匹配")
    else:
        _validate_text_content(ext, sample, fileobj)

    return {
        "original_name": filename,
        "extension": ext,
        "file_type": get_file_category(filename),
        "mime_type": get_mime_type(filename),
        "size": size,
        "hash": digest.hexdigest(),
    }


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
    return validate_fileobj(filename, io.BytesIO(content), max_size)
