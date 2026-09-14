"""P4-1 回归测试：上传扫描不得因只读取文件头部而漏检。

历史缺陷：
  * ``_validate_pdf`` 只取前 2MB 做活跃内容标记（/JavaScript、/Launch、
    /EmbeddedFile）匹配 —— 标记位于 2MB 之后即漏检；
  * ``scan_file`` 的内容扫描只读前 1MB —— 后部的可疑模式同样漏检。

修复口径：改为**有界流式**扫描（chunk + overlap），
扫全量但内存恒为 chunk+overlap，总量受 max_content_scan_bytes / max_file_size 约束。
"""
import pytest

from office_agent.security.file_security.file_scanner import (
    CONTENT_SCAN_OVERLAP,
    SCAN_CHUNK_SIZE,
    FileScanner,
    ThreatLevel,
    _iter_scan_windows,
)

PAYLOAD = b"/JavaScript"


def _make_pdf(path, payload_offset, payload=PAYLOAD, tail_pad=64):
    """构造结构合法（%PDF 头 + 尾部 %%EOF）的 PDF，payload 位于指定偏移。"""
    total = payload_offset + len(payload) + tail_pad
    buf = bytearray(b"A" * total)
    buf[0:8] = b"%PDF-1.7"
    buf[payload_offset:payload_offset + len(payload)] = payload
    buf[-8:] = b"\n%%EOF\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(buf))
    return path


# ------------------------------------------------------------ PDF


def test_pdf_payload_beyond_2mb_is_detected(tmp_path):
    """核心用例：payload 位于 2MB 之后，修复前必然漏检。"""
    path = _make_pdf(tmp_path / "late.pdf", 3 * 1024 * 1024)
    result = FileScanner().scan_file(path)
    assert result.is_blocked is True
    assert any("JavaScript" in t for t in result.detected_threats)
    assert result.threat_level == ThreatLevel.BLOCKED


def test_pdf_payload_at_very_end_is_detected(tmp_path):
    path = _make_pdf(tmp_path / "end.pdf", 2 * 1024 * 1024 + 100, tail_pad=16)
    result = FileScanner().scan_file(path)
    assert result.is_blocked is True


def test_pdf_payload_straddling_chunk_boundary(tmp_path):
    """签名横跨分片边界时，overlap 必须保证不被切断。"""
    offset = SCAN_CHUNK_SIZE - len(PAYLOAD) // 2
    path = _make_pdf(tmp_path / "straddle.pdf", offset)
    result = FileScanner().scan_file(path)
    assert result.is_blocked is True


@pytest.mark.parametrize("token", [
    b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/javascript",
])
def test_pdf_active_tokens_anywhere(tmp_path, token):
    path = _make_pdf(tmp_path / f"tok{token.decode()}.pdf",
                     2 * 1024 * 1024 + 4096, payload=token)
    result = FileScanner().scan_file(path)
    assert result.is_blocked is True


def test_clean_pdf_is_still_allowed(tmp_path):
    """无活跃内容的大 PDF 不得被误杀（防过度拦截）。"""
    path = _make_pdf(tmp_path / "clean.pdf", 3 * 1024 * 1024,
                     payload=b"AAAAAAAAAA")
    result = FileScanner().scan_file(path)
    assert result.is_blocked is False
    assert result.threat_level == ThreatLevel.SAFE


def test_incomplete_pdf_still_reported(tmp_path):
    """结构校验语义保持不变（魔数合法但缺 %%EOF）。"""
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"A" * 4096)
    result = FileScanner().scan_file(path)
    assert result.is_blocked is True
    assert any("PDF 结构不完整" in t for t in result.detected_threats)


# -------------------------------------------------------- 内容扫描


def test_suspicious_content_beyond_1mb_is_detected(tmp_path):
    """内容扫描不再只看前 1MB。"""
    path = tmp_path / "notes.txt"
    path.write_bytes(b"a" * (1024 * 1024 + 4096) + b"please run cmd.exe now")
    result = FileScanner().scan_file(path)
    assert result.is_blocked is True
    assert any("命令执行" in t for t in result.detected_threats)


def test_suspicious_content_at_chunk_overlap_is_detected(tmp_path):
    """模式正好落在分片边界上也要命中。"""
    offset = SCAN_CHUNK_SIZE - 3
    buf = bytearray(b"a" * (offset + 32))
    buf[offset:offset + 8] = b"cmd.exe "
    path = tmp_path / "edge.txt"
    path.write_bytes(bytes(buf))
    result = FileScanner().scan_file(path)
    assert result.is_blocked is True


def test_content_threats_are_not_duplicated_by_overlap(tmp_path):
    """overlap 会导致同一模式被重复命中，结果必须去重。"""
    path = tmp_path / "dup.txt"
    path.write_bytes(b"cmd.exe" + b"a" * 4096 + b"cmd.exe")
    result = FileScanner().scan_file(path)
    hits = [t for t in result.detected_threats if "命令执行" in t]
    assert len(hits) == 1


def test_clean_large_text_is_allowed(tmp_path):
    path = tmp_path / "clean.txt"
    path.write_bytes(b"hello world\n" * (200 * 1024))  # ~2.4MB
    result = FileScanner().scan_file(path)
    assert result.is_blocked is False
    assert result.threat_level == ThreatLevel.SAFE


def test_content_scan_budget_is_respected(tmp_path):
    """max_content_scan_bytes 是硬上界：超出部分确实不扫（证明有界，非无界）。"""
    path = tmp_path / "budget.txt"
    path.write_bytes(b"a" * 4096 + b"cmd.exe")
    scanner = FileScanner(max_content_scan_bytes=1024)
    result = scanner.scan_file(path)
    assert any("命令执行" in t for t in result.detected_threats) is False


def test_over_budget_is_scan_incomplete_and_blocked(tmp_path):
    """P2-58：超过扫描预算即 fail-closed，且 overlap 重复计数不得掩盖未扫尾部。"""
    mb = 1024 * 1024
    # 文件 = 2MB 预算 + 4KB（小于一个 overlap 8192）。若用 len(window) 累加，
    # overlap 会把 scanned 虚增到 >= 文件大小而误判“已扫完”；必须仍判未完整扫描。
    path = tmp_path / "incomplete.txt"
    path.write_bytes(b"benign\n" * ((2 * mb + 4096) // 7 + 1))
    scanner = FileScanner(max_content_scan_bytes=2 * mb)
    result = scanner.scan_file(path)
    assert any("SCAN_INCOMPLETE" in t for t in result.detected_threats)
    assert result.is_blocked is True
    assert result.is_safe is False


# -------------------------------------------------------- 流式窗口


class _CountingStream:
    def __init__(self, data):
        self._data = data
        self._pos = 0
        self.max_read = 0

    def read(self, n):
        self.max_read = max(self.max_read, n)
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


def test_iter_scan_windows_bounds_memory_and_covers_whole_file():
    data = b"A" * (SCAN_CHUNK_SIZE * 3 + 123)
    stream = _CountingStream(data)
    windows = list(_iter_scan_windows(stream, len(data), overlap=64))
    # 单次读取不超过 chunk（内存有界），且未把整份文件一次性读入
    assert stream.max_read <= SCAN_CHUNK_SIZE
    assert stream.max_read < len(data)
    # 覆盖完整内容（除首个窗口外，每个窗口开头 overlap 字节是重复的尾部）
    covered = windows[0] + b"".join(w[64:] for w in windows[1:])
    assert covered == data


def test_iter_scan_windows_respects_max_bytes():
    stream = _CountingStream(b"B" * 10_000)
    windows = list(_iter_scan_windows(stream, 1000))
    assert sum(len(w) for w in windows) == 1000


def test_iter_scan_windows_keeps_overlap_for_boundary_matches():
    marker = b"MARKER"
    data = b"A" * (SCAN_CHUNK_SIZE - 3) + marker + b"A" * 100
    stream = _CountingStream(data)
    hit = any(marker in w for w in _iter_scan_windows(
        stream, len(data), overlap=CONTENT_SCAN_OVERLAP))
    assert hit is True
