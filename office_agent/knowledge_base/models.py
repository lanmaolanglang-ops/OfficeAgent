"""
知识库数据模型
"""
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime, timezone


class DocumentType(Enum):
    """文档类型"""
    WORD_SPEC = "word_spec"         # Word 规范（论文/公文/报告格式）
    PPT_SPEC = "ppt_spec"           # PPT 规范（商务/答辩/发布）
    EXCEL_RULE = "excel_rule"       # Excel 业务规则
    GENERAL = "general"             # 通用知识


class ChunkStrategy(Enum):
    """切片策略"""
    PARAGRAPH = "paragraph"         # 按段落
    SECTION = "section"             # 按章节/标题
    FIXED = "fixed"                 # 固定长度
    SENTENCE = "sentence"           # 按句子


@dataclass
class KnowledgeChunk:
    """知识切片"""
    id: str = ""
    document_id: str = ""
    content: str = ""
    chunk_index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    # 位置信息
    section_title: str = ""
    page_number: int = 0
    # 向量（运行时填充，不持久化原始向量）
    embedding: Optional[List[float]] = field(default=None, repr=False)
    # 关键词（用于快速匹配）
    keywords: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.id:
            self.id = f"chunk_{uuid.uuid4().hex[:12]}"

    def to_dict(self, include_embedding: bool = False) -> dict:
        d = asdict(self)
        if not include_embedding:
            d.pop("embedding", None)
        return d


@dataclass
class KnowledgeDocument:
    """知识文档"""
    id: str = ""
    title: str = ""
    doc_type: str = "general"        # DocumentType
    source_path: str = ""
    content: str = ""
    chunks: List[KnowledgeChunk] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    chunk_count: int = 0

    def __post_init__(self):
        if not self.id:
            self.id = f"doc_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.chunk_count = len(self.chunks)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "doc_type": self.doc_type,
            "source_path": self.source_path,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "chunk_count": self.chunk_count,
        }


@dataclass
class SearchResult:
    """检索结果"""
    chunk: KnowledgeChunk = None
    score: float = 0.0
    document_title: str = ""
    document_id: str = ""
    matched_keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "content": self.chunk.content if self.chunk else "",
            "score": round(self.score, 4),
            "document_title": self.document_title,
            "section_title": self.chunk.section_title if self.chunk else "",
            "page_number": self.chunk.page_number if self.chunk else 0,
            "matched_keywords": self.matched_keywords,
            "metadata": self.chunk.metadata if self.chunk else {},
        }


@dataclass
class KnowledgeContext:
    """
    知识上下文 - 提供给 Agent 使用
    """
    query: str = ""
    results: List[SearchResult] = field(default_factory=list)
    doc_types: List[str] = field(default_factory=list)
    total_results: int = 0

    def to_text(self, max_results: int = 5) -> str:
        """转换为可插入 prompt 的文本"""
        if not self.results:
            return ""

        lines = [f"【知识库参考】（与「{self.query}」相关的规范/规则）"]
        for i, r in enumerate(self.results[:max_results], 1):
            source = r.document_title
            if r.chunk and r.chunk.section_title:
                source += f" - {r.chunk.section_title}"
            lines.append(f"\n[{i}] {source} (相关度: {r.score:.2f})")
            content = r.chunk.content if r.chunk else ""
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"    {content}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "total_results": self.total_results,
            "results": [r.to_dict() for r in self.results],
        }

    def has_content(self) -> bool:
        return len(self.results) > 0

    @property
    def best_score(self) -> float:
        return self.results[0].score if self.results else 0.0
