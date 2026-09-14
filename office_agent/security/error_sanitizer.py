"""Convert internal exceptions into safe, actionable user-facing messages."""
import re

_SECRET = re.compile(
    r"(?i)(api[_-]?key|token|authorization|password|secret)\s*[:=]\s*[^\s,;]+"
)
# Bearer/JWT 本体：`Authorization: Bearer eyJ...` 或裸 JWT（三段 base64url）
_BEARER = re.compile(
    r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*"
)
_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\[^\n\r]+")
_POSIX_PATH = re.compile(r"(?<![\w:])/[\w./-]{6,}")
_UNC_PATH = re.compile(r"\\\\[^\s\\]+\\[^\s\\]+")


def sanitize_error(error: object, default: str = "任务处理失败") -> str:
    text = str(error or "").strip()
    if not text:
        return default
    text = _SECRET.sub(lambda m: f"{m.group(1)}=[已隐藏]", text)
    text = _BEARER.sub("Bearer [已隐藏]", text)
    text = _JWT.sub("[已隐藏]", text)
    text = _WINDOWS_PATH.sub("[路径已隐藏]", text)
    text = _UNC_PATH.sub("[路径已隐藏]", text)
    text = _POSIX_PATH.sub("[路径已隐藏]", text)
    return text[:500]
