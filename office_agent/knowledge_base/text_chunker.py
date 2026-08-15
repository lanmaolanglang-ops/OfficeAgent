"""
文本切片器 - 将文档切分为适合检索的知识块
"""
import re
from typing import List, Optional
from dataclasses import dataclass

from .models import KnowledgeChunk, KnowledgeDocument
from .document_parser import ParsedDocument, ParsedSection


@dataclass
class ChunkConfig:
    """切片配置"""
    max_chunk_size: int = 500       # 最大字符数
    min_chunk_size: int = 10        # 最小字符数
    overlap: int = 50               # 重叠字符数
    strategy: str = "section"       # section/paragraph/fixed
    preserve_sections: bool = True  # 保留章节边界


class TextChunker:
    """文本切片器"""

    def __init__(self, config: Optional[ChunkConfig] = None):
        self.config = config or ChunkConfig()

    def chunk_document(self, doc: ParsedDocument,
                       doc_id: str = "") -> List[KnowledgeChunk]:
        """将解析后的文档切分为知识块"""
        chunks = []

        if self.config.strategy == "section":
            chunks = self._chunk_by_sections(doc, doc_id)
        elif self.config.strategy == "paragraph":
            chunks = self._chunk_by_paragraphs(doc, doc_id)
        elif self.config.strategy == "fixed":
            chunks = self._chunk_fixed(doc, doc_id)
        else:
            chunks = self._chunk_by_sections(doc, doc_id)

        # 过滤太短的块
        chunks = [c for c in chunks if len(c.content.strip()) >= self.config.min_chunk_size]

        # 重新编号
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i

        return chunks

    def _chunk_by_sections(self, doc: ParsedDocument,
                           doc_id: str) -> List[KnowledgeChunk]:
        """按章节切片"""
        chunks = []

        for section in doc.sections:
            content = section.content.strip()
            if not content:
                continue

            # 如果章节内容太长，进一步切分
            if len(content) > self.config.max_chunk_size:
                sub_chunks = self._split_long_text(content)
                for i, sub in enumerate(sub_chunks):
                    chunk = KnowledgeChunk(
                        document_id=doc_id,
                        content=sub,
                        section_title=section.title,
                        page_number=section.page_number,
                        metadata={
                            "section_level": section.level,
                            "sub_index": i,
                            "doc_title": doc.title,
                        }
                    )
                    chunks.append(chunk)
            else:
                # 标题 + 内容一起作为一个块
                full_content = content
                if section.title and section.title != "正文":
                    full_content = f"{section.title}\n{content}"

                chunk = KnowledgeChunk(
                    document_id=doc_id,
                    content=full_content,
                    section_title=section.title,
                    page_number=section.page_number,
                    metadata={
                        "section_level": section.level,
                        "doc_title": doc.title,
                    }
                )
                chunks.append(chunk)

        return chunks

    def _chunk_by_paragraphs(self, doc: ParsedDocument,
                             doc_id: str) -> List[KnowledgeChunk]:
        """按段落切片"""
        chunks = []
        current_title = doc.title
        current_page = 0

        # 合并所有文本，按段落切
        all_text = doc.full_text
        paragraphs = re.split(r'\n\s*\n', all_text)

        current_chunk = ""
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # 更新当前章节信息
            for section in doc.sections:
                if para.startswith(section.title):
                    current_title = section.title
                    current_page = section.page_number
                    break

            if len(current_chunk) + len(para) > self.config.max_chunk_size:
                if current_chunk:
                    chunks.append(KnowledgeChunk(
                        document_id=doc_id,
                        content=current_chunk.strip(),
                        section_title=current_title,
                        page_number=current_page,
                        metadata={"doc_title": doc.title},
                    ))
                    # 重叠
                    if self.config.overlap > 0:
                        current_chunk = current_chunk[-self.config.overlap:] + "\n" + para
                    else:
                        current_chunk = para
                else:
                    current_chunk = para
            else:
                current_chunk += "\n" + para if current_chunk else para

        if current_chunk.strip():
            chunks.append(KnowledgeChunk(
                document_id=doc_id,
                content=current_chunk.strip(),
                section_title=current_title,
                page_number=current_page,
                metadata={"doc_title": doc.title},
            ))

        return chunks

    def _chunk_fixed(self, doc: ParsedDocument,
                     doc_id: str) -> List[KnowledgeChunk]:
        """固定长度切片"""
        chunks = []
        text = doc.full_text
        size = self.config.max_chunk_size
        overlap = self.config.overlap

        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            # 尽量在句子边界切分
            if end < len(text):
                # 找最近的句号/换行
                for sep in ['\n', '。', '！', '？', '；', '.', '!', '?']:
                    pos = text.rfind(sep, start + size - 100, end)
                    if pos > start + 100:
                        end = pos + 1
                        break

            content = text[start:end].strip()
            if content:
                chunks.append(KnowledgeChunk(
                    document_id=doc_id,
                    content=content,
                    metadata={"doc_title": doc.title},
                ))

            start = end - overlap if overlap < end - start else end

        return chunks

    def _split_long_text(self, text: str) -> List[str]:
        """将长文本切分为小块"""
        chunks = []
        size = self.config.max_chunk_size
        overlap = self.config.overlap

        # 先按段落分
        paragraphs = re.split(r'\n\s*\n', text)
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current) + len(para) > size:
                if current:
                    chunks.append(current.strip())
                    if overlap > 0:
                        current = current[-overlap:] + "\n" + para
                    else:
                        current = para
                else:
                    # 单个段落超长，硬切
                    for i in range(0, len(para), size - overlap):
                        chunks.append(para[i:i + size])
                    current = ""
            else:
                current += "\n" + para if current else para

        if current.strip():
            chunks.append(current.strip())

        return chunks
