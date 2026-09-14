"""共享颜色解析工具。

Excel（openpyxl）与 PPT（python-pptx）各自需要库专属的颜色对象，
但十六进制解析与校验口径必须一致，否则同一输入在两个产品中
会得到不同的结果或不同的报错行为。本模块是唯一的解析入口：
- 接受 "#RGB"/"RGB"/"#RRGGBB"/"RRGGBB"（可选 8 位含 alpha）
- 非法输入统一抛 ValueError，消息包含原始输入
"""

from __future__ import annotations

import re

_HEX_RE = re.compile(r"[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?")


def _normalize_hex(value: str) -> str:
    """把输入归一化为 6 位 RRGGBB 或 8 位 AARRGGBB 大写串。

    只剥离**单个**前导 ``#``；``##FF0000`` 这类多井号属于非法输入，
    必须抛 ValueError 而不是被 ``lstrip("#")`` 静默吞成合法颜色（P4-2）。
    """
    if not isinstance(value, str):
        raise ValueError("颜色值必须是字符串")
    h = value.strip()
    if h.startswith("#"):
        h = h[1:]
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if not _HEX_RE.fullmatch(h):
        raise ValueError(f"无效的十六进制颜色: {value!r}")
    if len(h) == 6:
        h = f"FF{h}"
    return h.upper()


def parse_hex_color(value: str) -> tuple[int, int, int, int]:
    """解析十六进制颜色，返回 (r, g, b, a)，alpha 缺省为 255。

    Raises:
        ValueError: 输入不是字符串或不是合法的 3/6/8 位十六进制颜色。
    """
    h = _normalize_hex(value)
    return (
        int(h[2:4], 16),
        int(h[4:6], 16),
        int(h[6:8], 16),
        int(h[0:2], 16),
    )


def parse_hex_argb(value: str) -> str:
    """解析为大写 AARRGGBB 字符串（openpyxl Color(rgb=...) 直接可用）。"""
    return _normalize_hex(value)
