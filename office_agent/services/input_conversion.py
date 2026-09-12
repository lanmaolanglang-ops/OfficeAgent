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

# 本模块生成的临时转换副本统一前缀。
# 调用方据此区分"借用的用户原文件"与"本模块创建、需由调用方清理的临时副本"。
OWNED_TEMP_PREFIX = "office_agent_input_"


def is_text_like(path: str) -> bool:
    return os.path.splitext(path or "")[1].lower() in TEXT_LIKE_EXTENSIONS


def is_owned_temp_input(path: str) -> bool:
    """判断路径是否为本模块生成的临时转换副本（调用方负责清理）。

    必须同时满足"文件名带本模块前缀"与"位于系统临时目录"两个条件：
    只看前缀的话，一个恰好命名为 ``office_agent_input_xxx.docx`` 的用户上传
    文件会被误判成临时文件，进而在任务结束时被删除——那是不可逆的用户数据
    丢失。对非文本类输入，``ensure_docx_input`` 原样返回用户路径，这里必须
    给出明确的 False。
    """
    if not path:
        return False
    name = os.path.basename(path)
    if not name.startswith(OWNED_TEMP_PREFIX) or not name.endswith(".docx"):
        return False
    try:
        parent = os.path.realpath(os.path.dirname(os.path.abspath(path)))
        temp_root = os.path.realpath(tempfile.gettempdir())
    except OSError:
        return False
    return parent == temp_root


def ensure_docx_input(path: str) -> str:
    """返回可供 python-docx 消费的输入路径。

    非文本类输入原样返回（借用，调用方**不得**删除）；文本类输入解析章节
    结构后写成临时 .docx 并返回新路径——该临时文件由**调用方持有所有权**，
    任务结束后必须清理（用 ``is_owned_temp_input`` 判定所有权）。
    PDF 依赖可选的 pymupdf，缺失时给出明确错误而不是退化成乱码文本。
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

    fd, out_path = tempfile.mkstemp(
        suffix=".docx", prefix=OWNED_TEMP_PREFIX
    )
    os.close(fd)
    document.save(out_path)
    return out_path
