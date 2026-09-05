"""
Office Knowledge Base - 企业级知识库系统

让 Agent 能够检索办公规范、模板规则和专业知识。
支持导入 Word/PPT/Excel 规范文档，自动解析、切片、向量化、语义检索。
"""

from .models import (
    KnowledgeChunk,
    KnowledgeDocument,
    SearchResult,
    KnowledgeContext,
    DocumentType,
    ChunkStrategy,
)
from .document_parser import DocumentParser, ParsedDocument, ParsedSection
from .text_chunker import TextChunker, ChunkConfig
from .embeddings import (
    BaseEmbedder,
    HashingEmbedder,
    TfidfEmbedder,
    KeywordEmbedder,
    APIEmbedder,
    LocalSemanticEmbedder,
    EmbeddingError,
    EmbeddingBackendUnavailableError,
    InvalidEmbeddingVectorError,
    create_semantic_embedder,
    cosine_similarity,
    batch_cosine_similarity,
)
from .vector_store import VectorStore
from .knowledge_base import OfficeKnowledgeBase, create_default_kb

__all__ = [
    # Models
    "KnowledgeChunk", "KnowledgeDocument", "SearchResult",
    "KnowledgeContext", "DocumentType", "ChunkStrategy",
    # Parser
    "DocumentParser", "ParsedDocument", "ParsedSection",
    # Chunker
    "TextChunker", "ChunkConfig",
    # Embeddings
    "BaseEmbedder", "HashingEmbedder", "TfidfEmbedder", "KeywordEmbedder",
    "APIEmbedder", "LocalSemanticEmbedder",
    "EmbeddingError", "EmbeddingBackendUnavailableError",
    "InvalidEmbeddingVectorError", "create_semantic_embedder",
    "cosine_similarity", "batch_cosine_similarity",
    # Store
    "VectorStore",
    # Main
    "OfficeKnowledgeBase", "create_default_kb",
]
