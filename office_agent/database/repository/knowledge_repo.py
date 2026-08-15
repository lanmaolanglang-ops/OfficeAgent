"""知识库 Repository"""
from typing import Optional, List
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from .base import BaseRepository
from ..models.knowledge import Knowledge


class KnowledgeRepository(BaseRepository[Knowledge]):
    def __init__(self, session: Session):
        super().__init__(session, Knowledge)

    def get_by_category(self, category: str) -> List[Knowledge]:
        return self.find(category=category)

    def search_by_text(self, query: str, limit: int = 20) -> List[Knowledge]:
        """简单文本搜索（向量搜索由上层实现）"""
        like = f"%{query}%"
        stmt = select(Knowledge).where(
            or_(
                Knowledge.title.ilike(like),
                Knowledge.content.ilike(like),
            )
        ).limit(limit)
        return list(self.session.scalars(stmt))

    def add_knowledge(self, title: str, content: str, source: str = None,
                      source_type: str = None, category: str = None,
                      tags: str = None, embedding: str = None,
                      metadata_json: str = None,
                      chunk_index: int = 0, total_chunks: int = 1) -> Knowledge:
        kb = Knowledge(
            title=title, content=content, source=source,
            source_type=source_type, category=category, tags=tags,
            embedding=embedding, metadata_json=metadata_json,
            chunk_index=chunk_index, total_chunks=total_chunks,
        )
        return self.create(kb)

    def increment_usage(self, kb_id: str):
        kb = self.get_by_id(kb_id)
        if kb:
            self.update(kb_id, {"usage_count": kb.usage_count + 1})
