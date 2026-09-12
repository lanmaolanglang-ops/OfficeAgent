"""向量存储 - 存储知识切片和向量，支持语义与增量 lexical 检索。"""
import json
import math
import os
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .embeddings import BaseEmbedder, TfidfEmbedder
from .models import KnowledgeChunk, SearchResult


@dataclass
class StoredChunk:
    """存储的知识块"""
    chunk: KnowledgeChunk
    embedding: List[float] = field(default_factory=list)


class _DynamicEmbedding:
    """懒生成的零向量，长度始终跟随当前 vocabulary 维度。

    增量检索不再使用稠密向量；这个对象只为兼容旧代码里对
    ``len(stored.embedding)`` 的读取，避免新增 token 时回写全部历史 chunk。
    """

    def __init__(self, dimension_provider):
        self._dimension_provider = dimension_provider

    def __len__(self):
        return self._dimension_provider()

    def __iter__(self):
        return iter([0.0] * len(self))

    def __getitem__(self, index):
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return 0.0


class VectorStore:
    """向量存储。

    ``TfidfEmbedder`` 使用增量 inverted index：新增/删除 chunk 只更新该
    chunk 的 token postings 和 corpus 统计，不再重新 tokenize 历史文本。
    非 TF-IDF embedder 保留原批量 embedding 行为。
    """

    def __init__(self, embedder: Optional[BaseEmbedder] = None):
        self.embedder = embedder or TfidfEmbedder()
        self._chunks: List[StoredChunk] = []
        self._chunk_by_id: Dict[str, StoredChunk] = {}
        self._keyword_cache: Dict[str, set] = {}
        self._postings: Dict[str, set] = {}
        self._doc_tf: Dict[str, Counter] = {}
        self._df: Counter = Counter()
        self._fitted = False
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 增量索引内部状态
    # ------------------------------------------------------------------
    def _is_incremental(self) -> bool:
        return isinstance(self.embedder, TfidfEmbedder)

    def _tfidf(self) -> TfidfEmbedder:
        """返回当前 TF-IDF embedder；非增量 embedder 时快速失败。

        仅在 ``_is_incremental()`` 已为真的路径调用（已证明不变式）。
        """
        embedder = self.embedder
        if not isinstance(embedder, TfidfEmbedder):
            raise RuntimeError("当前 embedder 不支持增量 lexical 索引")
        return embedder

    def _tokenize(self, text: str) -> List[str]:
        return self._tfidf()._tokenize(text)

    def _idf(self, token: str) -> float:
        doc_count = len(self._doc_tf)
        document_frequency = self._df.get(token, 0)
        return math.log((doc_count + 1) / (document_frequency + 1)) + 1

    def _refresh_embedder_stats(self) -> None:
        """把当前 corpus 统计写回 TfidfEmbedder 兼容字段。"""
        if not self._is_incremental():
            return
        embedder = self._tfidf()
        embedder._doc_count = len(self._doc_tf)
        embedder.idf = {
            token: self._idf(token) for token in self._df
        }
        embedder._fitted = self._fitted

    def _index_chunk(self, chunk: KnowledgeChunk) -> None:
        tokens = self._tokenize(chunk.content)
        tf = Counter(tokens)
        self._doc_tf[chunk.id] = tf
        embedder = self._tfidf()
        for token in tf:
            self._postings.setdefault(token, set()).add(chunk.id)
            self._df[token] += 1
            if token not in embedder.vocabulary:
                embedder.vocabulary[token] = len(embedder.vocabulary)

    def _unindex_chunk(self, chunk_id: str) -> None:
        tf = self._doc_tf.pop(chunk_id, Counter())
        for token in tf:
            postings = self._postings.get(token)
            if postings is not None:
                postings.discard(chunk_id)
                if not postings:
                    self._postings.pop(token, None)
            self._df[token] -= 1
            if self._df[token] <= 0:
                self._df.pop(token, None)
        self._keyword_cache.pop(chunk_id, None)

    def _pad_embeddings(self) -> None:
        """兼容占位：动态 embedding 已经无需回写历史 chunk。"""

    def _new_embedding(self):
        if self._is_incremental():
            return _DynamicEmbedding(lambda: self.embedder.dimension)
        return []

    def rebuild_index(self) -> int:
        """从当前 chunk 文本重建整个 incremental index。"""
        with self._lock:
            self._postings.clear()
            self._doc_tf.clear()
            self._df.clear()
            self._chunk_by_id = {
                stored.chunk.id: stored for stored in self._chunks
            }
            embedder = self._tfidf()
            embedder.vocabulary = {}
            embedder.idf = {}
            self._keyword_cache.clear()
            for stored in self._chunks:
                stored.embedding = self._new_embedding()
                self._index_chunk(stored.chunk)
                self._cache_chunk_keywords(stored.chunk)
            self._fitted = bool(self._chunks)
            self._refresh_embedder_stats()
            self._pad_embeddings()
            return len(self._chunks)

    # ------------------------------------------------------------------
    # 增删改
    # ------------------------------------------------------------------
    def add_chunks(self, chunks: List[KnowledgeChunk]) -> int:
        if not chunks:
            return 0

        with self._lock:
            if self._is_incremental():
                if not self._fitted and self._chunks:
                    self.rebuild_index()
                for chunk in chunks:
                    existing = self._chunk_by_id.get(chunk.id)
                    if existing is not None:
                        self._unindex_chunk(chunk.id)
                        if existing in self._chunks:
                            self._chunks.remove(existing)
                    self._index_chunk(chunk)
                    self._cache_chunk_keywords(chunk)
                    stored = StoredChunk(chunk=chunk, embedding=self._new_embedding())
                    self._chunks.append(stored)
                    self._chunk_by_id[chunk.id] = stored
                self._fitted = True
                self._refresh_embedder_stats()
                self._pad_embeddings()
                return len(chunks)

            texts = [chunk.content for chunk in chunks]
            embeddings = self.embedder.embed(texts)
            for chunk, embedding in zip(chunks, embeddings):
                existing = self._chunk_by_id.get(chunk.id)
                if existing is not None:
                    # 与增量路径同一去重语义：同 id 覆盖写，不留重复项，
                    # 并丢弃该 id 的过期关键词缓存。
                    if existing in self._chunks:
                        self._chunks.remove(existing)
                    self._keyword_cache.pop(chunk.id, None)
                stored = StoredChunk(chunk=chunk, embedding=embedding)
                self._chunks.append(stored)
                self._chunk_by_id[chunk.id] = stored
                self._cache_chunk_keywords(chunk)
            self._fitted = True
            return len(chunks)

    def remove_chunks(self, chunk_ids: List[str]) -> int:
        if not chunk_ids:
            return 0
        with self._lock:
            if self._is_incremental():
                target_ids = set(chunk_ids)
                removed = 0
                for chunk_id in target_ids:
                    stored = self._chunk_by_id.pop(chunk_id, None)
                    if stored is None:
                        continue
                    self._unindex_chunk(chunk_id)
                    removed += 1
                self._chunks = [
                    item for item in self._chunks
                    if item.chunk.id not in target_ids
                ]
                if removed:
                    self._fitted = bool(self._chunks)
                    self._refresh_embedder_stats()
                    self._pad_embeddings()
                return removed

            remaining = [item for item in self._chunks if item.chunk.id not in chunk_ids]
            removed = len(self._chunks) - len(remaining)
            self._chunks = remaining
            self._chunk_by_id = {item.chunk.id: item for item in remaining}
            return removed

    def update_chunks(self, chunks: List[KnowledgeChunk]) -> int:
        """删除旧 chunk 并加入新 chunk，保持 identity 不变。"""
        self.remove_chunks([chunk.id for chunk in chunks])
        return self.add_chunks(chunks)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def search(self, query: str, top_k: int = 5,
               min_score: float = 0.05,
               doc_types: Optional[List[str]] = None,
               tags: Optional[List[str]] = None) -> List[SearchResult]:
        with self._lock:
            if not self._chunks:
                return []

            query_keywords = set(self._extract_keywords(query))
            if self._is_incremental():
                if not self._fitted:
                    self.rebuild_index()
                return self._incremental_search(
                    query, top_k, min_score, doc_types, tags, query_keywords,
                )

            query_vec = self.embedder.embed_query(query)
            if not query_vec:
                return self._keyword_search(query, top_k)
            results = []
            for stored in self._chunks:
                if not self._matches_filters(stored.chunk, doc_types, tags):
                    continue
                vec_score = self._cosine(query_vec, stored.embedding)
                keyword_score = self._keyword_match_score(query_keywords, stored.chunk)
                final_score = vec_score + keyword_score * 0.15
                if final_score >= min_score:
                    results.append(self._result_for(stored, final_score, query_keywords))
            results.sort(key=lambda item: item.score, reverse=True)
            return results[:top_k]

    def _incremental_search(self, query: str, top_k: int,
                            min_score: float, doc_types, tags,
                            query_keywords: set) -> List[SearchResult]:
        query_tf = Counter(self._tokenize(query))
        query_weights = {
            token: count * self._idf(token)
            for token, count in query_tf.items()
        }
        query_norm_sq = sum(weight * weight for weight in query_weights.values())
        query_norm = math.sqrt(query_norm_sq)

        candidate_ids = set()
        for token in set(query_tf) | query_keywords:
            candidate_ids.update(self._postings.get(token, set()))
        if not candidate_ids:
            return []

        results = []
        for chunk_id in candidate_ids:
            stored = self._chunk_by_id.get(chunk_id)
            if stored is None:
                continue
            if not self._matches_filters(stored.chunk, doc_types, tags):
                continue
            vec_score = self._incremental_cosine(
                stored.chunk.id, query_weights, query_norm,
            )
            keyword_score = self._keyword_match_score(query_keywords, stored.chunk)
            final_score = vec_score + keyword_score * 0.15
            if final_score >= min_score:
                results.append(self._result_for(stored, final_score, query_keywords))
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    def _incremental_cosine(self, chunk_id: str, query_weights: dict,
                            query_norm: float) -> float:
        doc_tf: Counter = self._doc_tf.get(chunk_id, Counter())
        if not doc_tf or query_norm == 0:
            return 0.0
        numerator = 0.0
        doc_norm_sq = 0.0
        for token, count in doc_tf.items():
            token_idf = self._idf(token)
            doc_weight = count * token_idf
            doc_norm_sq += doc_weight * doc_weight
            numerator += query_weights.get(token, 0.0) * doc_weight
        doc_norm = math.sqrt(doc_norm_sq)
        if doc_norm == 0:
            return 0.0
        return numerator / (query_norm * doc_norm)

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _matches_filters(self, chunk: KnowledgeChunk, doc_types, tags) -> bool:
        if doc_types:
            chunk_type = chunk.metadata.get("doc_type", "")
            if chunk_type and chunk_type not in doc_types:
                return False
        if tags:
            chunk_tags = chunk.metadata.get("tags", [])
            if not any(tag in chunk_tags for tag in tags):
                return False
        return True

    def _keyword_search(self, query: str, top_k: int = 5) -> List[SearchResult]:
        keywords = set(self._extract_keywords(query))
        results = []
        for stored in self._chunks:
            score = self._keyword_match_score(keywords, stored.chunk)
            if score > 0:
                results.append(self._result_for(stored, score, keywords))
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

    def _keyword_match_score(self, query_keywords: set,
                             chunk: KnowledgeChunk) -> float:
        if not query_keywords:
            return 0.0
        chunk_words = self._cache_chunk_keywords(chunk)
        matched = query_keywords & chunk_words
        return len(matched) / max(1, len(query_keywords))

    def _cache_chunk_keywords(self, chunk: KnowledgeChunk) -> set:
        cached = self._keyword_cache.get(chunk.id)
        if cached is None:
            cached = set(chunk.keywords) | set(self._extract_keywords(chunk.content))
            self._keyword_cache[chunk.id] = cached
        return cached

    def _result_for(self, stored: StoredChunk, score: float,
                    query_keywords: set) -> SearchResult:
        matched = list(query_keywords & self._cache_chunk_keywords(stored.chunk))
        return SearchResult(
            chunk=stored.chunk,
            score=score,
            document_title=stored.chunk.metadata.get("doc_title", ""),
            document_id=stored.chunk.document_id,
            matched_keywords=matched,
        )

    def _extract_keywords(self, text: str) -> List[str]:
        import re
        keywords = []
        chinese = re.findall(r'[\u4e00-\u9fff]', text)
        for i in range(len(chinese) - 1):
            keywords.append(chinese[i] + chinese[i + 1])
        keywords.extend(re.findall(r'[a-zA-Z]{2,}', text.lower()))
        return keywords

    # ------------------------------------------------------------------
    # 统计与持久化
    # ------------------------------------------------------------------
    @property
    def count(self) -> int:
        return len(self._chunks)

    def clear(self):
        with self._lock:
            self._chunks.clear()
            self._chunk_by_id.clear()
            self._keyword_cache.clear()
            self._postings.clear()
            self._doc_tf.clear()
            self._df.clear()
            self._fitted = False
            if self._is_incremental():
                embedder = self._tfidf()
                embedder.vocabulary = {}
                embedder.idf = {}
                embedder._doc_count = 0
                embedder._fitted = False

    def save(self, path: str):
        with self._lock:
            data: dict = {
                "chunks": [],
                "fitted": self._fitted,
            }
            for stored in self._chunks:
                chunk_data = stored.chunk.to_dict(include_embedding=False)
                chunk_data["embedding"] = (
                    list(stored.embedding)
                    if self._is_incremental() else stored.embedding
                )
                data["chunks"].append(chunk_data)

            if self._is_incremental():
                self._refresh_embedder_stats()
                embedder = self._tfidf()
                data["embedder_type"] = "tfidf"
                data["vocabulary"] = embedder.vocabulary
                data["idf"] = embedder.idf
                data["doc_count"] = embedder._doc_count

            os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)

    def load(self, path: str) -> int:
        if not os.path.exists(path):
            return 0
        with self._lock:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)

            self.clear()
            if data.get("embedder_type") == "tfidf" and self._is_incremental():
                embedder = self._tfidf()
                embedder.vocabulary = data.get("vocabulary", {})
                embedder.idf = data.get("idf", {})
                embedder._doc_count = data.get("doc_count", 0)

            for raw_chunk in data.get("chunks", []):
                chunk_data = dict(raw_chunk)
                raw_embedding = chunk_data.pop("embedding", [])
                chunk = KnowledgeChunk(**{
                    key: value for key, value in chunk_data.items()
                    if key in KnowledgeChunk.__dataclass_fields__
                })
                if self._is_incremental():
                    embedding = self._new_embedding()
                else:
                    # 稠密/远端 embedding：复用持久化向量，不伪造零向量。
                    embedding = list(raw_embedding) if raw_embedding else []
                stored = StoredChunk(chunk=chunk, embedding=embedding)
                self._chunks.append(stored)
                self._chunk_by_id[chunk.id] = stored
                self._cache_chunk_keywords(chunk)

            if self._is_incremental():
                # TF-IDF：chunks 是 source of truth，重建 lexical inverted index。
                self.rebuild_index()
            else:
                # 非 TF-IDF embedder 不支持增量 lexical 索引：向量已随 chunk
                # 持久化，这里只重建 id 映射；绝不能调用只属于 TfidfEmbedder
                # 的 _tfidf()（会直接 raise）。
                self._chunk_by_id = {sc.chunk.id: sc for sc in self._chunks}
                self._fitted = bool(self._chunks)
            return len(self._chunks)
