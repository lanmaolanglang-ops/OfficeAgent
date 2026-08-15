"""
File Security Scanner - 文件安全扫描
检查文件类型、大小、扩展名、内容安全性
"""
from __future__ import annotations

import os
import re
import mimetypes
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


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
    ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    ".pdf", ".txt", ".csv", ".rtf", ".odt", ".ods", ".odp",
    # 图片
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
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


class FileScanner:
    """文件安全扫描器"""

    def __init__(self,
                 max_file_size: int = 100 * 1024 * 1024,  # 100MB
                 allowed_extensions: set[str] | None = None,
                 blocked_extensions: set[str] | None = None,
                 scan_content: bool = True):
        self.max_file_size = max_file_size
        self.allowed_extensions = allowed_extensions or ALLOWED_EXTENSIONS
        self.blocked_extensions = blocked_extensions or DANGEROUS_EXTENSIONS
        self.scan_content = scan_content

    def scan_file(self, filepath: str | Path, filename: str | None = None) -> ScanResult:
        """扫描文件"""
        filepath = Path(filepath)
        if filename is None:
            filename = filepath.name

        threats = []
        ext = filepath.suffix.lower()
        file_size = filepath.stat().st_size if filepath.exists() else 0
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

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

        # 4. 内容扫描
        suspicious_count = 0
        if self.scan_content and filepath.exists():
            try:
                # 只扫描前1MB
                with open(filepath, "rb") as f:
                    content = f.read(1024 * 1024)
                # 尝试解码为文本
                try:
                    text = content.decode("utf-8", errors="ignore")
                    for pattern, desc in SUSPICIOUS_PATTERNS:
                        if re.search(pattern, text):
                            threats.append(f"可疑内容: {desc}")
                            suspicious_count += 1
                except Exception:
                    pass
            except Exception:
                pass

        # 判定威胁等级
        if suspicious_count >= 3:
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
            except Exception:
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
        return True
