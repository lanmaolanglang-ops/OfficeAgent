"""Convert internal exceptions into safe, actionable user-facing messages."""
import re

_SECRET = re.compile(r"(?i)(api[_-]?key|token|authorization|password|secret)\s*[:=]\s*[^\s,;]+")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\[^\n\r]+")


def sanitize_error(error: object, default: str = "任务处理失败") -> str:
    text = str(error or "").strip()
    if not text:
        return default
    text = _SECRET.sub(lambda m: f"{m.group(1)}=[已隐藏]", text)
    text = _WINDOWS_PATH.sub("[路径已隐藏]", text)
    return text[:500]
