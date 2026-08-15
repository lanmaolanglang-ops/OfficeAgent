"""长期记忆 Repository"""
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.orm import Session

from .base import BaseRepository
from ..models.memory import Memory


class MemoryRepository(BaseRepository[Memory]):
    def __init__(self, session: Session):
        super().__init__(session, Memory)

    def get_by_user(self, user_id: str, active_only: bool = True) -> List[Memory]:
        if active_only:
            return self.find(user_id=user_id, is_active=True)
        return self.find(user_id=user_id)

    def get_by_type(self, memory_type: str, user_id: str = None) -> List[Memory]:
        if user_id:
            return self.find(memory_type=memory_type, user_id=user_id, is_active=True)
        return self.find(memory_type=memory_type, is_active=True)

    def get_by_key(self, key: str, user_id: str = None) -> Optional[Memory]:
        if user_id:
            return self.find_one(key=key, user_id=user_id)
        return self.find_one(key=key)

    def add_memory(self, content: str, memory_type: str = "fact",
                   user_id: str = None, key: str = None,
                   importance: float = 0.5, category: str = None,
                   tags: str = None, source: str = "auto_learned",
                   metadata_json: str = None) -> Memory:
        mem = Memory(
            content=content, memory_type=memory_type, user_id=user_id,
            key=key, importance=importance, category=category,
            tags=tags, source=source, metadata_json=metadata_json,
        )
        return self.create(mem)

    def access(self, memory_id: str):
        """记录访问"""
        mem = self.get_by_id(memory_id)
        if mem:
            self.update(memory_id, {"access_count": mem.access_count + 1})

    def deactivate(self, memory_id: str):
        self.update(memory_id, {"is_active": False})

    def get_important(self, user_id: str = None, threshold: float = 0.7,
                      limit: int = 50) -> List[Memory]:
        stmt = select(Memory).where(
            Memory.importance >= threshold,
            Memory.is_active == True,
        )
        if user_id:
            stmt = stmt.where(Memory.user_id == user_id)
        stmt = stmt.order_by(Memory.importance.desc()).limit(limit)
        return list(self.session.scalars(stmt))
