"""
File Security Scanner - 文件安全扫描
检查文件类型、大小、扩展名、内容安全性
"""
from __future__ import annotations

import os
import re
import mimetypes
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath


class ThreatLevel(str, Enum):
    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    DANGEROUS = "dangerous"
    BLOCKED = "blocked"


@dataclass
class ScanResult:
    """扫描结果"""
    filename: str
    file_size: int
    threat_level: ThreatLevel
    is_allowed: bool
    detected_threats: list[str] = field(default_factory=list)
    mime_type: str = ""
    extension: str = ""
    details: dict = field(default_factory=dict)

    @property
    def is_safe(self) -> bool:
        return self.threat_level == ThreatLevel.SAFE

    @property
    def is_blocked(self) -> bool:
        return self.threat_level in (ThreatLevel.DANGEROUS, ThreatLevel.BLOCKED)


# 允许的Office文件扩展名
ALLOWED_EXTENSIONS: set[str] = {
    # Office
    ".docx", ".pptx", ".xlsx",
    ".pdf", ".txt", ".csv", ".rtf", ".odt", ".ods", ".odp",
    # 图片
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    # 数据
    ".json", ".xml", ".yaml", ".yml", ".md",
    # 压缩
    ".zip",
}

# 危险扩展名（直接阻止）
DANGEROUS_EXTENSIONS: set[str] = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".psm1",
    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta",
    ".dll", ".sys", ".drv", ".bin", ".sh", ".bash", ".zsh",
    ".py", ".pyc", ".pyo", ".pyd",  # Python脚本（在沙箱外不允许）
    ".jar", ".class", ".apk", ".app",
    ".reg", ".inf", ".ini", ".cfg",
    ".htm", ".html", ".php", ".asp", ".aspx", ".jsp",
}

# 可疑文件内容模式
SUSPICIOUS_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)<?xml.*\bDOCTYPE\b.*\bENTITY\b", "XML外部实体注入(XXE)"),
    (r"(?i)AutoOpen|Document_Open|Auto_Open", "Office宏自动执行"),
    (r"(?i)Shell\(|WScript\.Shell|CreateObject", "ActiveX/Shell对象"),
    (r"(?i)cmd\.exe|powershell\.exe|/bin/sh|/bin/bash", "命令执行"),
    (r"(?i)eval\(|exec\(|compile\(", "动态代码执行"),
    (r"(?i)subprocess|os\.system|os\.popen", "系统命令调用"),
    (r"(?i)http://|https://", "外部URL（可能泄露数据）"),
    (r"(?i)password|passwd|secret|api_key|token", "敏感信息"),
]

# 文件魔数（用于验证文件类型）
MAGIC_NUMBERS: dict[str, bytes] = {
    ".docx": b"PK\x03\x04",
    ".xlsx": b"PK\x03\x04",
    ".pptx": b"PK\x03\x04",
    ".pdf": b"%PDF",
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".gif": b"GIF8",
    ".zip": b"PK\x03\x04",
}

ARCHIVE_EXTENSIONS = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".zip"}
OOXML_REQUIRED_PARTS: dict[str, set[str]] = {
    ".docx": {"[content_types].xml", "_rels/.rels", "word/document.xml"},
    ".xlsx": {"[content_types].xml", "_rels/.rels", "xl/workbook.xml"},
    ".pptx": {"[content_types].xml", "_rels/.rels", "ppt/presentation.xml"},
}
ODF_MIME_TYPES = {
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
}
PDF_ACTIVE_TOKENS = (b"/javascript", b"/js", b"/launch", b"/embeddedfile")


class FileScanner:
    """文件安全扫描器"""

    def __init__(self,
                 max_file_size: int = 100 * 1024 * 1024,  # 100MB
                 allowed_extensions: set[str] | None = None,
                 blocked_extensions: set[str] | None = None,
                 scan_content: bool = True,
                 max_archive_members: int = 5000,
                 max_archive_uncompressed_size: int | None = None,
                 max_archive_member_size: int | None = None,
                 max_compression_ratio: float = 250.0):
        self.max_file_size = max_file_size
        self.allowed_extensions = allowed_extensions or ALLOWED_EXTENSIONS
        self.blocked_extensions = blocked_extensions or DANGEROUS_EXTENSIONS
        self.scan_content = scan_content
        self.max_archive_members = max_archive_members
        self.max_archive_uncompressed_size = (
            max_archive_uncompressed_size or max_file_size * 5
        )
        self.max_archive_member_size = max_archive_member_size or max_file_size * 2
        self.max_compression_ratio = max_compression_ratio
        if min(max_archive_members, self.max_archive_uncompressed_size,
               self.max_archive_member_size) <= 0 or max_compression_ratio <= 0:
            raise ValueError("压缩包安全限制必须大于 0")

    @staticmethod
    def _unsafe_archive_name(name: str) -> bool:
        normalized = name.replace("\\", "/")
        path = PurePosixPath(normalized)
        return (
            normalized.startswith("/")
            or bool(path.parts and ":" in path.parts[0])
            or ".." in path.parts
            or any(ord(char) < 32 for char in normalized)
        )

    def _validate_archive(self, filepath: Path, ext: str) -> list[str]:
        """Validate metadata before decompressing any archive member."""
        threats: list[str] = []
        try:
            with zipfile.ZipFile(filepath) as archive:
                members = archive.infolist()
                if len(members) > self.max_archive_members:
                    return ["压缩包成员数超限"]

                seen: set[str] = set()
                files: set[str] = set()
                total_size = 0
                for member in members:
                    name = member.filename.replace("\\", "/")
                    normalized = name.lower()
                    if self._unsafe_archive_name(name):
                        threats.append("压缩包包含路径穿越或绝对路径")
                        break
                    if normalized in seen:
                        threats.append("压缩包包含重复成员名")
                        break
                    seen.add(normalized)
                    if not member.is_dir():
                        files.add(normalized)
                    if member.flag_bits & 0x1:
                        threats.append("压缩包包含无法扫描的加密成员")
                        break
                    unix_mode = member.external_attr >> 16
                    if unix_mode and (unix_mode & 0o170000) == 0o120000:
                        threats.append("压缩包包含符号链接")
                        break
                    if member.file_size > self.max_archive_member_size:
                        threats.append("压缩包单个成员解压后过大")
                        break
                    total_size += member.file_size
                    if total_size > self.max_archive_uncompressed_size:
                        threats.append("压缩包解压后总体积超限")
                        break
                    ratio = member.file_size / max(member.compress_size, 1)
                    if member.file_size >= 1024 * 1024 and ratio > self.max_compression_ratio:
                        threats.append("压缩包压缩比异常，疑似压缩炸弹")
                        break
                if threats:
                    return threats

                if ext in OOXML_REQUIRED_PARTS:
                    if not OOXML_REQUIRED_PARTS[ext].issubset(files):
                        threats.append(f"OOXML 包结构与 {ext} 不匹配")
                    if any(
                        "vbaproject" in name
                        or name.endswith((".exe", ".dll", ".js", ".vbs", ".ps1"))
                        for name in seen
                    ):
                        threats.append("OOXML 包含宏或可执行部件")
                elif ext in ODF_MIME_TYPES:
                    try:
                        mime_info = archive.getinfo("mimetype")
                        if mime_info.file_size > 256:
                            raise ValueError
                        actual_mime = archive.read(mime_info).decode("ascii")
                    except (KeyError, UnicodeDecodeError, ValueError):
                        actual_mime = ""
                    if actual_mime != ODF_MIME_TYPES[ext] or "content.xml" not in files:
                        threats.append(f"ODF 包结构与 {ext} 不匹配")

                # Inspect only small XML metadata after size/ratio checks.
                xml_scan_budget = 8 * 1024 * 1024
                for member in members:
                    if xml_scan_budget <= 0:
                        break
                    if member.filename.lower().endswith((".xml", ".rels")):
                        read_size = min(64 * 1024, xml_scan_budget)
                        with archive.open(member) as stream:
                            prefix = stream.read(read_size).lower()
                        xml_scan_budget -= len(prefix)
                        if b"<!doctype" in prefix or b"<!entity" in prefix:
                            threats.append("压缩包 XML 包含 DTD/ENTITY 声明")
                            break
        except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
            threats.append("压缩包损坏或使用不支持的算法")
        return threats

    @staticmethod
    def _validate_pdf(filepath: Path) -> list[str]:
        try:
            with filepath.open("rb") as stream:
                header = stream.read(16)
                stream.seek(max(0, filepath.stat().st_size - 8192))
                tail = stream.read(8192)
                stream.seek(0)
                sample = stream.read(min(filepath.stat().st_size, 2 * 1024 * 1024)).lower()
        except OSError:
            return ["PDF 文件无法读取"]
        if not re.match(br"%PDF-\d\.\d", header) or b"%%EOF" not in tail:
            return ["PDF 结构不完整"]
        if any(token in sample for token in PDF_ACTIVE_TOKENS):
            return ["PDF 包含 JavaScript、启动动作或嵌入文件"]
        return []

    @staticmethod
    def _validate_image(filepath: Path, ext: str) -> list[str]:
        try:
            from PIL import Image, UnidentifiedImageError
        except ImportError:
            return ["当前环境无法执行图片结构校验"]

        try:
            expected = {
                ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".gif": "GIF",
                ".bmp": "BMP", ".webp": "WEBP",
            }[ext]
            with Image.open(filepath) as image:
                if image.format != expected:
                    return [f"图片容器与 {ext} 不匹配"]
                image.verify()
        except (OSError, ValueError, UnidentifiedImageError,
                Image.DecompressionBombError):
            return ["图片结构损坏或不可识别"]
        return []

    def scan_file(self, filepath: str | Path, filename: str | None = None) -> ScanResult:
        """扫描文件"""
        filepath = Path(filepath)
        if filename is None:
            filename = filepath.name

        threats = []
        ext = filepath.suffix.lower()
        file_size = filepath.stat().st_size if filepath.exists() else 0
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        if not self.is_filename_safe(filename):
            return ScanResult(
                filename=filename, file_size=file_size,
                threat_level=ThreatLevel.BLOCKED, is_allowed=False,
                detected_threats=["不安全的文件名"], mime_type=mime_type,
                extension=ext,
            )

        # 1. 检查扩展名
        if ext in self.blocked_extensions:
            threats.append(f"危险扩展名: {ext}")
            return ScanResult(
                filename=filename, file_size=file_size,
                threat_level=ThreatLevel.BLOCKED, is_allowed=False,
                detected_threats=threats, mime_type=mime_type, extension=ext,
            )

        if self.allowed_extensions and ext not in self.allowed_extensions:
            threats.append(f"不允许的扩展名: {ext}")
            return ScanResult(
                filename=filename, file_size=file_size,
                threat_level=ThreatLevel.BLOCKED, is_allowed=False,
                detected_threats=threats, mime_type=mime_type, extension=ext,
            )

        if not filepath.is_file():
            return ScanResult(
                filename=filename, file_size=file_size,
                threat_level=ThreatLevel.BLOCKED, is_allowed=False,
                detected_threats=["文件不存在或不是普通文件"],
                mime_type=mime_type, extension=ext,
            )

        # 2. 检查文件大小
        if file_size > self.max_file_size:
            threats.append(f"文件过大: {file_size} > {self.max_file_size} bytes")
            return ScanResult(
                filename=filename, file_size=file_size,
                threat_level=ThreatLevel.BLOCKED, is_allowed=False,
                detected_threats=threats, mime_type=mime_type, extension=ext,
            )

        # 3. 验证魔数
        if filepath.exists() and ext in MAGIC_NUMBERS:
            with open(filepath, "rb") as f:
                header = f.read(8)
            expected = MAGIC_NUMBERS[ext]
            if not header.startswith(expected):
                threats.append(f"文件类型不匹配: 期望{ext}但内容不符")
                return ScanResult(
                    filename=filename, file_size=file_size,
                    threat_level=ThreatLevel.BLOCKED, is_allowed=False,
                    detected_threats=threats, mime_type=mime_type, extension=ext,
                )

        # Validate container metadata and essential structures, not only magic bytes.
        if filepath.exists() and ext in ARCHIVE_EXTENSIONS:
            threats.extend(self._validate_archive(filepath, ext))
        elif filepath.exists() and ext == ".pdf":
            threats.extend(self._validate_pdf(filepath))
        elif filepath.exists() and ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
            threats.extend(self._validate_image(filepath, ext))

        if filepath.exists() and ext in ARCHIVE_EXTENSIONS | {
            ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
        }:
            if threats:
                return ScanResult(
                    filename=filename, file_size=file_size,
                    threat_level=ThreatLevel.BLOCKED, is_allowed=False,
                    detected_threats=threats, mime_type=mime_type, extension=ext,
                )

        # 4. 内容扫描
        suspicious_count = 0
        dangerous_content = False
        if self.scan_content and filepath.exists():
            try:
                # 只扫描前1MB
                with open(filepath, "rb") as f:
                    content = f.read(1024 * 1024)
                # 尝试解码为文本
                text = content.decode("utf-8", errors="ignore")
                for pattern_index, (pattern, desc) in enumerate(SUSPICIOUS_PATTERNS):
                    if re.search(pattern, text):
                        threats.append(f"可疑内容: {desc}")
                        suspicious_count += 1
                        if pattern_index in {1, 2, 3, 4, 5}:
                            dangerous_content = True
            except OSError:
                threats.append("文件内容无法读取")
                dangerous_content = True

        # 判定威胁等级
        if dangerous_content:
            level = ThreatLevel.DANGEROUS
            allowed = False
        elif suspicious_count >= 1:
            level = ThreatLevel.SUSPICIOUS
            allowed = True  # 可疑但允许，需要进一步检查
        elif threats:
            level = ThreatLevel.SUSPICIOUS
            allowed = True
        else:
            level = ThreatLevel.SAFE
            allowed = True

        return ScanResult(
            filename=filename, file_size=file_size,
            threat_level=level, is_allowed=allowed,
            detected_threats=threats, mime_type=mime_type, extension=ext,
        )

    def scan_bytes(self, data: bytes, filename: str) -> ScanResult:
        """扫描字节数据"""
        import tempfile
        ext = Path(filename).suffix.lower()
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(data)
            tmp_path = f.name
        try:
            result = self.scan_file(tmp_path, filename)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                # The temporary file is outside the application storage and a
                # failed cleanup must not turn a blocked scan into success.
                pass
        return result

    def is_filename_safe(self, filename: str) -> bool:
        """检查文件名是否安全（防止路径遍历）"""
        # 禁止路径分隔符
        if "/" in filename or "\\" in filename:
            return False
        # 禁止..
        if ".." in filename:
            return False
        # 禁止控制字符
        if any(ord(c) < 32 for c in filename):
            return False
        # 禁止以.开头（隐藏文件）
        if filename.startswith("."):
            return False
        # Windows 会折叠尾点/尾空格，并把保留设备名映射到特殊对象。
        if filename.endswith((".", " ")):
            return False
        stem = Path(filename).stem.rstrip(". ").upper()
        reserved = {"CON", "PRN", "AUX", "NUL", "CLOCK$"}
        reserved.update({f"COM{i}" for i in range(1, 10)})
        reserved.update({f"LPT{i}" for i in range(1, 10)})
        if stem in reserved:
            return False
        return True
