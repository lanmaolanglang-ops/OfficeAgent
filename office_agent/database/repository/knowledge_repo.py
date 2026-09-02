"""知识库 Repository"""
from typing import List
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from .base import BaseRepository
from ..models.knowledge import Knowledge


class KnowledgeRepository(BaseRepository[Knowledge]):
    def __init__(self, session: Session):
        super().__init__(session, Knowledge)

    def get_by_category(self, category: str, offset: int = 0,
                        limit: int = 100) -> List[Knowledge]:
        return self.find(offset=offset, limit=limit, category=category)

    def search_by_text(self, query: str, limit: int = 20) -> List[Knowledge]:
        """简单文本搜索（向量搜索由上层实现）"""
        escaped = str(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        stmt = select(Knowledge).where(
            or_(
                Knowledge.title.ilike(like, escape="\\"),
                Knowledge.content.ilike(like, escape="\\"),
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
        from sqlalchemy import update
        return self.session.execute(
            update(Knowledge).where(Knowledge.id == kb_id)
            .values(usage_count=Knowledge.usage_count + 1)
            .execution_options(synchronize_session="fetch")
        ).rowcount
