"""文本类输入（.txt/.md/.pdf）到 .docx 的统一转换。

WordService 基于 python-docx，只能消费 .docx。本模块在 Word 任务入口
把文本类输入一次性转换为临时 .docx，API 路由因此可以把这些扩展名
直接派发给 word_agent（清单 273 的转换链）。转换是全仓唯一接缝：
不要在 Agent 内部再各自实现文本读取。
"""
from __future__ import annotations

import os
import tempfile

TEXT_LIKE_EXTENSIONS = (".txt", ".md", ".pdf")


def is_text_like(path: str) -> bool:
    return os.path.splitext(path or "")[1].lower() in TEXT_LIKE_EXTENSIONS


def ensure_docx_input(path: str) -> str:
    """返回可供 python-docx 消费的输入路径。

    非文本类输入原样返回；文本类输入解析章节结构后写成临时 .docx
    并返回新路径。PDF 依赖可选的 pymupdf，缺失时给出明确错误而不是
    退化成乱码文本。
    """
    ext = os.path.splitext(path or "")[1].lower()
    if ext not in TEXT_LIKE_EXTENSIONS:
        return path
    if ext == ".pdf":
        try:
            import pymupdf  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "处理 PDF 输入需要安装 pymupdf（pip install pymupdf）"
            ) from exc

    from ..knowledge_base.document_parser import DocumentParser
    parsed = DocumentParser().parse(path)

    from docx import Document
    document = Document()
    if parsed.title:
        document.add_heading(parsed.title, level=1)
    wrote_content = False
    for section in parsed.sections:
        if section.title and section.level >= 1 and section.title != parsed.title:
            document.add_heading(section.title, level=min(section.level + 1, 9))
        for line in (section.content or "").split("\n"):
            line = line.strip()
            if line:
                document.add_paragraph(line)
                wrote_content = True
    if not wrote_content and parsed.full_text.strip():
        for line in parsed.full_text.split("\n"):
            line = line.strip()
            if line:
                document.add_paragraph(line)

    fd, out_path = tempfile.mkstemp(suffix=".docx", prefix="office_agent_input_")
    os.close(fd)
    document.save(out_path)
    return out_path
