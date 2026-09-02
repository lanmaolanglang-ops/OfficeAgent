"""Repository 基类"""
from typing import TypeVar, Generic, Type, Optional, List, Any, Dict
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """通用 Repository 基类"""

    def __init__(self, session: Session, model: Type[ModelType]):
        self.session = session
        self.model = model

    def get_by_id(self, id: str) -> Optional[ModelType]:
        return self.session.get(self.model, id)

    @staticmethod
    def _page(offset: int, limit: int) -> tuple[int, int]:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset 必须是非负整数")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit 必须在 1 到 1000 之间")
        return offset, limit

    def _column(self, name: str):
        if not isinstance(name, str) or name not in self.model.__mapper__.attrs:
            raise ValueError(f"未知过滤字段: {name}")
        return getattr(self.model, name)

    def get_all(self, offset: int = 0, limit: int = 100) -> List[ModelType]:
        offset, limit = self._page(offset, limit)
        stmt = select(self.model).offset(offset).limit(limit)
        return list(self.session.scalars(stmt))

    def count(self, **filters) -> int:
        stmt = select(func.count()).select_from(self.model)
        for key, value in filters.items():
            stmt = stmt.where(self._column(key) == value)
        return self.session.scalar(stmt) or 0

    def create(self, obj: ModelType) -> ModelType:
        self.session.add(obj)
        self.session.flush()
        return obj

    def create_from_dict(self, data: Dict[str, Any]) -> ModelType:
        obj = self.model(**data)
        return self.create(obj)

    def update(self, id: str, data: Dict[str, Any]) -> Optional[ModelType]:
        obj = self.get_by_id(id)
        if obj:
            for key, value in data.items():
                self._column(key)
                setattr(obj, key, value)
            self.session.flush()
        return obj

    def delete(self, id: str) -> bool:
        obj = self.get_by_id(id)
        if obj:
            self.session.delete(obj)
            self.session.flush()
            return True
        return False

    def find_one(self, **filters) -> Optional[ModelType]:
        stmt = select(self.model)
        for key, value in filters.items():
            stmt = stmt.where(self._column(key) == value)
        return self.session.scalar(stmt)

    def find(self, offset: int = 0, limit: int = 100, order_by=None,
             descending: bool = False, **filters) -> List[ModelType]:
        offset, limit = self._page(offset, limit)
        stmt = select(self.model)
        for key, value in filters.items():
            stmt = stmt.where(self._column(key) == value)
        if order_by is not None:
            col = self._column(order_by)
            stmt = stmt.order_by(col.desc() if descending else col.asc())
        stmt = stmt.offset(offset).limit(limit)
        return list(self.session.scalars(stmt))
