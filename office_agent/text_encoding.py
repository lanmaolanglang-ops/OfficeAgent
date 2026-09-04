"""统一的纯文本编码探测入口。

背景：仓库中曾有多处各自维护的朴素 ``for encoding in (...): try decode``
路径（document_parser、word_service、slide_planner），存在两类问题：

1. gb18030 几乎能对任意字节序列"成功 decode"，因此朴素的顺序探测会
   过早接受乱码结果（典型：UTF-8 内容混入非法字节后整体落入 gb18030，
   产出含大量私有区字符的"涓"式乱码）。
2. 各入口编码列表与兜底策略不一致（有的 latin-1 静默兜底，有的抛错）。

本模块提供唯一探测入口：

- BOM 优先（UTF-8 BOM / UTF-16 LE/BE BOM），BOM 是权威信号；
- 其次 UTF-8 strict（UTF-8 解码无歧义，成功即可信）；
- 再用 gb18030（gb18030 解码兼容 gbk / gb2312 双字节区，单一候选即可
  覆盖整个 GB 家族），但对结果做轻量乱码质量评分：
  私有区（PUA）、控制字符、替换符、罕用 CJK 扩展区字符加权计分，
  密度超过阈值则拒绝接受，显式抛出 :class:`TextDecodeError`，
  绝不用 ``errors="ignore"`` 静默丢字节。
"""

from __future__ import annotations

import codecs
import os

__all__ = ["TextDecodeError", "decode_bytes", "read_text_file"]


class TextDecodeError(ValueError):
    """无法以任何受支持编码可靠解码文本。"""


# BOM 探测顺序：UTF-8 BOM 必须先于 UTF-16（前缀互不冲突，但保持显式顺序）。
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

# gb18030 解码兼容 gbk / gb2312，单一候选覆盖整个 GB 家族。
_CJK_CANDIDATES: tuple[str, ...] = ("gb18030",)

# 乱码质量评分阈值：可疑字符加权密度超过该值则拒绝接受解码结果。
_MAX_MOJIBAKE_SCORE = 0.02

# 最低绝对罚分：短文本字符数少，少量罕用字符（如 gb18030 四字节生僻字）
# 就会推高密度。只有绝对罚分也足够高时才拒绝，避免误拒合法的短文本。
_MIN_REJECT_PENALTY = 6


def _mojibake_penalty(text: str) -> int:
    """计算解码结果的乱码罚分（0 表示完全正常）。

    权重设计原则：正常中文/英文/混排文本几乎不触发罚分；
    典型"错误编码成功 decode"产物（UTF-8 落入 GBK 的"涓"式乱码）
    会高频命中私有区 / 罕用扩展区，罚分远超阈值。
    """
    penalty = 0
    for ch in text:
        cp = ord(ch)
        if cp == 0xFFFD:  # 显式替换符
            penalty += 4
        elif cp < 0x20 and ch not in "\n\r\t\f\v":  # 非常规控制字符
            penalty += 4
        elif 0xE000 <= cp <= 0xF8FF:  # 私有区（PUA），乱码高频命中
            penalty += 3
        elif 0x3400 <= cp <= 0x4DBF:  # CJK 扩展 A，现代文本罕用
            penalty += 1
        elif 0xF900 <= cp <= 0xFAFF:  # CJK 兼容表意文字
            penalty += 1
        elif cp >= 0x20000:  # 扩展 B 及以后平面（gb18030 四字节生僻字合法，低权重）
            penalty += 1
    return penalty


def _mojibake_score(text: str) -> float:
    """乱码罚分密度（罚分 / 字符数），供测试与诊断使用。"""
    if not text:
        return 0.0
    return _mojibake_penalty(text) / len(text)


def _is_mojibake(text: str) -> bool:
    """判断解码结果是否疑似乱码（密度与绝对罚分双重门槛）。"""
    if not text:
        return False
    penalty = _mojibake_penalty(text)
    return penalty >= _MIN_REJECT_PENALTY and penalty / len(text) > _MAX_MOJIBAKE_SCORE


def decode_bytes(data: bytes) -> tuple[str, str]:
    """探测编码并解码字节串，返回 ``(文本, 实际使用的编码)``。

    Raises:
        TextDecodeError: 所有候选编码均无法给出可信结果。
    """
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                return data.decode(encoding), encoding
            except UnicodeDecodeError as exc:
                raise TextDecodeError(
                    f"文件带有 {encoding} BOM 但内容无法按该编码解码"
                ) from exc

    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    for encoding in _CJK_CANDIDATES:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not _is_mojibake(text):
            return text, encoding

    raise TextDecodeError(
        "无法识别文本编码：非 UTF-8/UTF-16，按 GB 家族解码的结果疑似乱码"
    )


def read_text_file(file_path: str | os.PathLike[str]) -> str:
    """读取文本文件并自动探测编码，返回解码后的文本。

    Raises:
        TextDecodeError: 无法可靠识别编码。
    """
    with open(file_path, "rb") as f:
        data = f.read()
    return decode_bytes(data)[0]
