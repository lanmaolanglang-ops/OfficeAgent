"""
向量存储 - 存储知识切片和向量，支持语义检索
"""
import os
import json
import math
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from .models import KnowledgeChunk, SearchResult
from .embeddings import TfidfEmbedder, BaseEmbedder, cosine_similarity


@dataclass
class StoredChunk:
    """存储的知识块"""
    chunk: KnowledgeChunk
    embedding: List[float] = field(default_factory=list)


class VectorStore:
    """
    向量存储

    支持：
    - 添加知识块和向量
    - 语义检索（余弦相似度）
    - 关键词检索（混合）
    - 持久化/加载
    """

    def __init__(self, embedder: Optional[BaseEmbedder] = None):
        self.embedder = embedder or TfidfEmbedder()
        self._chunks: List[StoredChunk] = []
        self._keyword_cache: Dict[str, set] = {}
        self._fitted = False

    def add_chunks(self, chunks: List[KnowledgeChunk]) -> int:
        """添加知识块并计算向量"""
        if not chunks:
            return 0

        # 本地可训练的 embedder 每次都基于完整语料重建。TF-IDF 的词汇表和
        # IDF 会随新文档变化，只在首批数据上 fit 会令后续文档接近零向量。
        if hasattr(self.embedder, 'fit'):
            all_chunks = [stored.chunk for stored in self._chunks] + list(chunks)
            texts = [c.content for c in all_chunks]
            self.embedder.fit(texts)
            self._fitted = True
            embeddings = self.embedder.embed(texts)
            self._chunks = []
            self._keyword_cache.clear()
            for chunk, emb in zip(all_chunks, embeddings):
                if hasattr(self.embedder, 'get_keywords'):
                    chunk.keywords = self.embedder.get_keywords(chunk.content, top_k=15)
                self._chunks.append(StoredChunk(chunk=chunk, embedding=emb))
                self._cache_chunk_keywords(chunk)
        else:
            texts = [c.content for c in chunks]
            embeddings = self.embedder.embed(texts)
            for chunk, emb in zip(chunks, embeddings):
                self._chunks.append(StoredChunk(chunk=chunk, embedding=emb))
                self._cache_chunk_keywords(chunk)

        return len(chunks)

    def search(self, query: str, top_k: int = 5,
               min_score: float = 0.05,
               doc_types: Optional[List[str]] = None,
               tags: Optional[List[str]] = None) -> List[SearchResult]:
        """
        语义检索

        Args:
            query: 查询文本
            top_k: 返回前 K 个
            min_score: 最低相似度阈值
            doc_types: 过滤文档类型
            tags: 过滤标签
        """
        if not self._chunks:
            return []

        # 查询向量化
        query_vec = self.embedder.embed_query(query)
        if not query_vec:
            return self._keyword_search(query, top_k)

        # 计算相似度
        results = []
        query_keywords = set(self._extract_keywords(query))

        for stored in self._chunks:
            # 类型过滤
            if doc_types:
                chunk_type = stored.chunk.metadata.get("doc_type", "")
                if chunk_type and chunk_type not in doc_types:
                    continue

            # 标签过滤
            if tags:
                chunk_tags = stored.chunk.metadata.get("tags", [])
                if not any(t in chunk_tags for t in tags):
                    continue

            # 向量相似度
            vec_score = cosine_similarity(query_vec, stored.embedding)

            # 关键词加分
            keyword_score = self._keyword_match_score(query_keywords, stored.chunk)

            # 混合分数：向量为主，关键词加分
            final_score = vec_score + keyword_score * 0.15

            if final_score >= min_score:
                matched = list(query_keywords & self._cache_chunk_keywords(stored.chunk))
                results.append(SearchResult(
                    chunk=stored.chunk,
                    score=final_score,
                    document_title=stored.chunk.metadata.get("doc_title", ""),
                    document_id=stored.chunk.document_id,
                    matched_keywords=matched,
                ))

        # 按分数排序
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    def _keyword_search(self, query: str, top_k: int = 5) -> List[SearchResult]:
        """纯关键词检索（当向量不可用时）"""
        keywords = set(self._extract_keywords(query))
        results = []

        for stored in self._chunks:
            score = self._keyword_match_score(keywords, stored.chunk)
            if score > 0:
                matched = list(keywords & self._cache_chunk_keywords(stored.chunk))
                results.append(SearchResult(
                    chunk=stored.chunk,
                    score=score,
                    document_title=stored.chunk.metadata.get("doc_title", ""),
                    document_id=stored.chunk.document_id,
                    matched_keywords=matched,
                ))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    def _keyword_match_score(self, query_keywords: set,
                             chunk: KnowledgeChunk) -> float:
        """关键词匹配分数"""
        if not query_keywords:
            return 0.0
        chunk_words = self._cache_chunk_keywords(chunk)
        matched = query_keywords & chunk_words
        return len(matched) / max(1, len(query_keywords))

    def _cache_chunk_keywords(self, chunk: KnowledgeChunk) -> set:
        """每个知识块只提取一次关键词，避免每次查询重复扫描正文。"""
        cached = self._keyword_cache.get(chunk.id)
        if cached is None:
            cached = set(chunk.keywords) | set(self._extract_keywords(chunk.content))
            self._keyword_cache[chunk.id] = cached
        return cached

    def _extract_keywords(self, text: str) -> List[str]:
        """简单关键词提取"""
        import re
        keywords = []
        # 中文 bigram
        chinese = re.findall(r'[\u4e00-\u9fff]', text)
        for i in range(len(chinese) - 1):
            keywords.append(chinese[i] + chinese[i + 1])
        # 英文词
        keywords.extend(re.findall(r'[a-zA-Z]{2,}', text.lower()))
        return keywords

    @property
    def count(self) -> int:
        return len(self._chunks)

    def clear(self):
        self._chunks.clear()
        self._keyword_cache.clear()
        self._fitted = False

    def save(self, path: str):
        """持久化到文件"""
        data = {
            "chunks": [],
            "fitted": self._fitted,
        }
        for stored in self._chunks:
            chunk_data = stored.chunk.to_dict(include_embedding=False)
            chunk_data["embedding"] = stored.embedding
            data["chunks"].append(chunk_data)

        # 保存词汇表（如果是 TF-IDF）
        if hasattr(self.embedder, 'vocabulary'):
            data["embedder_type"] = "tfidf"
            data["vocabulary"] = self.embedder.vocabulary
            data["idf"] = self.embedder.idf
            data["doc_count"] = self.embedder._doc_count

        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self, path: str) -> int:
        """从文件加载"""
        if not os.path.exists(path):
            return 0

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 恢复 embedder
        if data.get("embedder_type") == "tfidf" and hasattr(self.embedder, 'vocabulary'):
            self.embedder.vocabulary = data.get("vocabulary", {})
            self.embedder.idf = data.get("idf", {})
            self.embedder._doc_count = data.get("doc_count", 0)
            fitted = bool(data.get("fitted", False) and self.embedder.vocabulary)
            self._fitted = fitted
            if hasattr(self.embedder, "_fitted"):
                self.embedder._fitted = fitted

        # 恢复 chunks
        self._chunks.clear()
        self._keyword_cache.clear()
        for chunk_data in data.get("chunks", []):
            emb = chunk_data.pop("embedding", [])
            chunk = KnowledgeChunk(**{
                k: v for k, v in chunk_data.items()
                if k in KnowledgeChunk.__dataclass_fields__
            })
            self._chunks.append(StoredChunk(chunk=chunk, embedding=emb))
            self._cache_chunk_keywords(chunk)

        return len(self._chunks)
