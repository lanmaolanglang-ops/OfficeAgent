"""RAG / 知识库后台任务。"""
import hashlib
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from ...security.error_sanitizer import sanitize_error

logger = logging.getLogger("office_agent.tasks.rag")


def _public_source(source: str | None, file_path: str) -> str:
    """Keep useful provenance without persisting a local absolute path."""
    candidate = source or file_path
    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"}:
        return candidate
    return Path(candidate).name


def _progress(progress, value: int, message: str) -> None:
    if progress:
        progress.update(value, message)


def _load_all(repo, page_size: int = 1000):
    """Read a repository in bounded pages instead of bypassing its page guard."""
    items = []
    offset = 0
    while True:
        page = repo.get_all(offset=offset, limit=page_size)
        items.extend(page)
        if len(page) < page_size:
            return items
        offset += page_size


def _load_filtered(repo, *, page_size: int = 1000, **filters):
    """Page through a filtered repository query without its default limit."""
    items = []
    offset = 0
    while True:
        page = repo.find(offset=offset, limit=page_size, **filters)
        items.extend(page)
        if len(page) < page_size:
            return items
        offset += page_size


def _load_search_candidates(repo, category: str | None,
                            max_candidates: int, page_size: int = 1000):
    """Load at most max_candidates and report whether more rows exist."""
    items = []
    offset = 0
    target = max_candidates + 1
    while len(items) < target:
        limit = min(page_size, target - len(items))
        if category:
            page = repo.get_by_category(category, offset=offset, limit=limit)
        else:
            page = repo.get_all(offset=offset, limit=limit)
        items.extend(page)
        if len(page) < limit:
            break
        offset += len(page)
    return items[:max_candidates], len(items) > max_candidates


def _document_fingerprint(chunks, title: str, category: str | None,
                          source: str | None) -> str:
    digest = hashlib.sha256()
    for value in (title, category or "", source or ""):
        digest.update(value.encode("utf-8", errors="replace"))
        digest.update(b"\0")
    for chunk in chunks:
        digest.update(chunk.content.encode("utf-8", errors="replace"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def _chunks_from_file(file_path: str, title: str | None, category: str | None):
    from ...knowledge_base.document_parser import DocumentParser
    from ...knowledge_base.text_chunker import TextChunker

    parsed = DocumentParser().parse(file_path, doc_type=category or "general")
    resolved_title = title or parsed.title or Path(file_path).stem
    chunks = TextChunker().chunk_document(parsed, doc_id=file_path)
    if not chunks:
        raise ValueError("文档没有可索引的文本内容")
    return resolved_title, chunks


def _chunks_from_text(text: str, title: str | None, category: str | None):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("待嵌入文本不能为空")
    from ...knowledge_base.document_parser import DocumentParser
    from ...knowledge_base.text_chunker import TextChunker

    resolved_title = title or "直接输入"
    parsed = DocumentParser().parse_text(
        text, title=resolved_title, doc_type=category or "general"
    )
    chunks = TextChunker().chunk_document(parsed, doc_id=resolved_title)
    if not chunks:
        raise ValueError("文本没有可索引的有效内容")
    return resolved_title, chunks


def _store_chunks(chunks, title: str, category: str | None,
                  source: str | None = None) -> int:
    """Persist chunks using a restart-stable vector space."""
    from ...database.repository import KnowledgeRepository
    from ...database.session import session_scope
    from ...knowledge_base.embeddings import create_semantic_embedder

    fingerprint = _document_fingerprint(chunks, title, category, source)
    embedder = create_semantic_embedder()
    embeddings = embedder.embed([chunk.content for chunk in chunks])
    with session_scope() as session:
        repo = KnowledgeRepository(session)
        existing = _load_filtered(
            repo, title=title, category=category, source=source
        )
        matching = []
        for item in existing:
            try:
                metadata = json.loads(item.metadata_json or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if metadata.get("document_fingerprint") == fingerprint:
                matching.append(item)
        if len(matching) == len(chunks) and {
                item.chunk_index for item in matching
        } == set(range(len(chunks))):
            # 幂等重新索引：现有行改为当前 semantic model，避免遗留旧
            # hashing 向量继续留在库里被误当成可比较的 semantic 向量。
            for item, embedding in zip(
                    sorted(matching, key=lambda item: item.chunk_index),
                    embeddings):
                try:
                    existing_metadata = json.loads(item.metadata_json or "{}")
                except (TypeError, json.JSONDecodeError):
                    existing_metadata = {}
                item.embedding = json.dumps(embedding)
                item.embedding_model = embedder.model_id
                item.metadata_json = json.dumps({
                    "embedding_model": embedder.model_id,
                    "embedding_version": embedder.version,
                    "embedding_dimension": embedder.dimension,
                    "document_fingerprint": fingerprint,
                    "section_title": existing_metadata.get("section_title", ""),
                    "page_number": existing_metadata.get("page_number", 0),
                }, ensure_ascii=False)
            logger.info("刷新重复知识索引: %s (%s chunks)", title, len(chunks))
            return len(chunks)

        for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            repo.add_knowledge(
                title=title,
                content=chunk.content,
                source=source,
                source_type="document" if source else "manual",
                category=category,
                embedding=json.dumps(embedding),
                embedding_model=embedder.model_id,
                metadata_json=json.dumps({
                    "embedding_model": embedder.model_id,
                    "embedding_version": embedder.version,
                    "embedding_dimension": embedder.dimension,
                    "section_title": chunk.section_title,
                    "page_number": chunk.page_number,
                    "document_fingerprint": fingerprint,
                }, ensure_ascii=False),
                chunk_index=index,
                total_chunks=len(chunks),
            )
    return len(chunks)


LEGACY_EMBEDDING_MODEL_IDS = {"hashing-512-v1"}


def _item_embedding_metadata(item):
    try:
        metadata = json.loads(item.metadata_json or "{}")
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return metadata


def _item_embedding_model(item) -> str:
    metadata = _item_embedding_metadata(item)
    return (
        getattr(item, "embedding_model", None)
        or metadata.get("embedding_model")
        or "hashing-512-v1"
    )


def _item_embedding_dimension(item) -> int:
    metadata = _item_embedding_metadata(item)
    value = metadata.get("embedding_dimension")
    if isinstance(value, int):
        return value
    try:
        vector = json.loads(item.embedding or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        vector = []
    return len(vector) if isinstance(vector, list) else 0


def index_document(file_path: str, title: str = None,
                   category: str = None, source: str = None,
                   options: dict = None, progress=None,
                   _task_id: str = None, **kwargs) -> dict:
    """解析、切片、向量化并持久化一个文档。"""
    result = {"status": "success", "chunks": 0, "title": title}
    try:
        _progress(progress, 5, "初始化索引")
        if not file_path or not os.path.isfile(file_path):
            raise FileNotFoundError(file_path or "")
        _progress(progress, 15, "解析文档")
        resolved_title, chunks = _chunks_from_file(file_path, title, category)
        result["title"] = resolved_title
        _progress(progress, 55, "生成向量")
        result["chunks"] = _store_chunks(
            chunks, resolved_title, category, _public_source(source, file_path)
        )
        _progress(progress, 100, f"索引完成，共 {result['chunks']} 个片段")
        logger.info("文档索引 %s 完成: %s, %s chunks",
                    _task_id, resolved_title, result["chunks"])
    except Exception as exc:
        result.update(status="failed", error=sanitize_error(exc))
        logger.error("文档索引 %s 失败: %s", _task_id, result["error"])
    return result


def chunk_and_embed(text: str, title: str = None,
                    category: str = None, progress=None,
                    _task_id: str = None, **kwargs) -> dict:
    """切片、向量化并持久化直接输入的文本。"""
    result = {"status": "success", "chunks": 0, "title": title}
    try:
        _progress(progress, 10, "文本切片")
        resolved_title, chunks = _chunks_from_text(text, title, category)
        _progress(progress, 40, "生成向量")
        result["chunks"] = _store_chunks(chunks, resolved_title, category)
        result["title"] = resolved_title
        _progress(progress, 100, "完成")
    except Exception as exc:
        result.update(status="failed", error=sanitize_error(exc))
        logger.error("文本嵌入 %s 失败: %s", _task_id, result["error"])
    return result


def search_knowledge(query: str, top_k: int = 5,
                     category: str = None, max_candidates: int = 10000,
                     progress=None,
                     _task_id: str = None, **kwargs) -> dict:
    """使用持久化向量执行余弦检索，并回退到文本匹配。"""
    result = {
        "status": "success", "results": [], "candidate_count": 0,
        "candidate_limit": 0, "truncated": False, "rebuild_required": False,
        "legacy_count": 0, "incompatible_count": 0, "embedding_model": None,
    }
    try:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("检索词不能为空")
        top_k = max(1, min(int(top_k), 50))
        max_candidates = max(1, min(int(max_candidates), 100000))
        result["candidate_limit"] = max_candidates
        _progress(progress, 20, "准备检索条件")
        from ...database.repository import KnowledgeRepository
        from ...database.session import session_scope
        from ...knowledge_base.embeddings import (
            create_semantic_embedder, cosine_similarity,
        )

        embedder = create_semantic_embedder()
        result["embedding_model"] = embedder.model_id
        query_vector = embedder.embed_query(query)
        scored = []
        with session_scope() as session:
            repo = KnowledgeRepository(session)
            items, truncated = _load_search_candidates(
                repo, category, max_candidates
            )
            result["candidate_count"] = len(items)
            result["truncated"] = truncated
            if truncated:
                logger.warning(
                    "知识检索候选超过上限: task=%s category=%s limit=%s",
                    _task_id, category, max_candidates,
                )
            for item in items:
                lexical_match = (
                    query.lower() in (item.content or "").lower()
                    or query.lower() in (item.title or "").lower()
                )
                try:
                    vector = json.loads(item.embedding or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    vector = []
                legacy = _item_embedding_model(item) in LEGACY_EMBEDDING_MODEL_IDS
                compatible = (
                    _item_embedding_model(item) == embedder.model_id
                    and _item_embedding_dimension(item) == embedder.dimension
                )
                if legacy or not compatible:
                    # 旧 hashing 或 model/version/dimension 不一致的向量
                    # 绝不与当前 semantic query vector 做余弦比较。
                    score = 0.35 if lexical_match else 0.0
                    result["rebuild_required"] = True
                    if legacy:
                        result["legacy_count"] += 1
                    else:
                        result["incompatible_count"] += 1
                else:
                    score = cosine_similarity(query_vector, vector)
                    if lexical_match:
                        score = max(score, 0.35)
                scored.append((score, item))

            for score, item in sorted(
                    scored, key=lambda pair: pair[0], reverse=True)[:top_k]:
                result["results"].append({
                    "id": item.id,
                    "title": item.title,
                    "content": item.content[:500],
                    "source": item.source,
                    "category": item.category,
                    "score": round(float(score), 6),
                })
        _progress(progress, 100, f"找到 {len(result['results'])} 条结果")
    except Exception as exc:
        result.update(status="failed", error=sanitize_error(exc))
        logger.error("知识检索 %s 失败: %s", _task_id, result["error"])
    return result


def refresh_knowledge_base(progress=None, _task_id: str = None, **kwargs) -> dict:
    """幂等重算全部持久化向量。"""
    result = {"status": "success", "refreshed": 0, "total": 0}
    try:
        _progress(progress, 20, "扫描知识库")
        from ...database.repository import KnowledgeRepository
        from ...database.session import session_scope
        from ...knowledge_base.embeddings import create_semantic_embedder

        with session_scope() as session:
            repo = KnowledgeRepository(session)
            items = _load_all(repo)
            embedder = create_semantic_embedder()
            vectors = embedder.embed([item.content for item in items])
            for item, vector in zip(items, vectors):
                item.embedding = json.dumps(vector)
                item.embedding_model = embedder.model_id
                metadata = _item_embedding_metadata(item)
                metadata["embedding_model"] = embedder.model_id
                metadata["embedding_version"] = embedder.version
                metadata["embedding_dimension"] = embedder.dimension
                item.metadata_json = json.dumps(metadata, ensure_ascii=False)
            result["total"] = len(items)
            result["refreshed"] = len(items)
        _progress(progress, 100, f"已刷新 {result['refreshed']} 条向量")
        logger.info("知识库刷新 %s: %s 条记录",
                    _task_id, result["refreshed"])
    except Exception as exc:
        result.update(status="failed", error=sanitize_error(exc))
        logger.error("知识库刷新 %s 失败: %s", _task_id, result["error"])
    return result
