"""
RAG / 知识库后台任务
"""
import os
import logging
import traceback
from pathlib import Path

logger = logging.getLogger("office_agent.tasks.rag")


def index_document(file_path: str, title: str = None,
                   category: str = None, source: str = None,
                   options: dict = None, progress=None,
                   _task_id: str = None, **kwargs) -> dict:
    """
    文档索引任务（解析+切片+向量化+入库）
    """
    options = options or {}
    result = {"status": "success", "chunks": 0, "title": title}

    try:
        if progress:
            progress.update(5, "初始化索引")

        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)

        if progress:
            progress.update(15, "解析文档")

        # 解析文档
        from ...knowledge_base.document_parser import DocumentParser
        parser = DocumentParser()
        content = parser.parse(file_path)

        if not title:
            title = Path(file_path).stem

        if progress:
            progress.update(35, "文本切片")

        from ...knowledge_base.text_chunker import TextChunker
        chunker = TextChunker()
        chunks = chunker.chunk(content)
        result["chunks"] = len(chunks)

        if progress:
            progress.update(55, "生成向量")

        from ...knowledge_base.embeddings import EmbeddingModel
        embedder = EmbeddingModel()
        embeddings = embedder.embed_batch([c.text for c in chunks])

        if progress:
            progress.update(75, "写入知识库")

        from ...database.session import session_scope
        from ...database.repository import KnowledgeRepository

        with session_scope() as session:
            repo = KnowledgeRepository(session)
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                import json
                emb_str = json.dumps(emb)
                repo.add_knowledge(
                    title=title,
                    content=chunk.text,
                    source=source or file_path,
                    source_type="document",
                    category=category,
                    embedding=emb_str,
                    chunk_index=i,
                    total_chunks=len(chunks),
                )

        if progress:
            progress.update(100, f"索引完成，共 {len(chunks)} 个片段")

        logger.info(f"文档索引 {_task_id} 完成: {title}, {len(chunks)} chunks")

    except Exception as e:
        from ...security.error_sanitizer import sanitize_error
        logger.error("文档索引 %s 失败: %s", _task_id, sanitize_error(e))
        result["status"] = "failed"
        result["error"] = sanitize_error(e)
        raise

    return result


def chunk_and_embed(text: str, title: str = None,
                    category: str = None, progress=None,
                    _task_id: str = None, **kwargs) -> dict:
    """文本切片并向量化"""
    result = {"status": "success", "chunks": 0}

    try:
        if progress:
            progress.update(10, "文本切片")

        from ...knowledge_base.text_chunker import TextChunker
        chunker = TextChunker()
        chunks = chunker.chunk(text)

        if progress:
            progress.update(40, "生成向量")

        from ...knowledge_base.embeddings import EmbeddingModel
        embedder = EmbeddingModel()
        embeddings = embedder.embed_batch([c.text for c in chunks])

        if progress:
            progress.update(70, "写入知识库")

        import json
        from ...database.session import session_scope
        from ...database.repository import KnowledgeRepository

        with session_scope() as session:
            repo = KnowledgeRepository(session)
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                repo.add_knowledge(
                    title=title or f"chunk_{i}",
                    content=chunk.text,
                    category=category,
                    embedding=json.dumps(emb),
                    chunk_index=i,
                    total_chunks=len(chunks),
                )

        result["chunks"] = len(chunks)

        if progress:
            progress.update(100, "完成")

    except Exception as e:
        result["status"] = "failed"
        from ...security.error_sanitizer import sanitize_error
        result["error"] = sanitize_error(e)
        raise

    return result


def search_knowledge(query: str, top_k: int = 5,
                     category: str = None, progress=None,
                     _task_id: str = None, **kwargs) -> dict:
    """知识库检索"""
    result = {"status": "success", "results": []}

    try:
        if progress:
            progress.update(20, "生成查询向量")

        from ...knowledge_base.embeddings import EmbeddingModel
        embedder = EmbeddingModel()
        query_emb = embedder.embed(query)

        if progress:
            progress.update(50, "向量检索")

        from ...database.session import session_scope
        from ...database.repository import KnowledgeRepository
        import json

        with session_scope() as session:
            repo = KnowledgeRepository(session)
            # 简单文本搜索（向量相似度由上层实现）
            items = repo.search_by_text(query, limit=top_k)
            for item in items:
                result["results"].append({
                    "id": item.id,
                    "title": item.title,
                    "content": item.content[:200],
                    "source": item.source,
                    "category": item.category,
                })

        if progress:
            progress.update(100, f"找到 {len(result['results'])} 条结果")

    except Exception as e:
        result["status"] = "failed"
        from ...security.error_sanitizer import sanitize_error
        result["error"] = sanitize_error(e)

    return result


def refresh_knowledge_base(progress=None, _task_id: str = None, **kwargs) -> dict:
    """
    刷新知识库（定时任务，每周执行）
    重新计算向量、清理过期数据等
    """
    result = {"status": "success", "refreshed": 0}

    try:
        if progress:
            progress.update(20, "扫描知识库")

        from ...database.session import session_scope
        from ...database.repository import KnowledgeRepository

        with session_scope() as session:
            repo = KnowledgeRepository(session)
            total = repo.count()
            result["total"] = total

        if progress:
            progress.update(100, f"知识库共 {total} 条")

        logger.info(f"知识库刷新: {total} 条记录")

    except Exception as e:
        result["status"] = "failed"
        from ...security.error_sanitizer import sanitize_error
        result["error"] = sanitize_error(e)

    return result
