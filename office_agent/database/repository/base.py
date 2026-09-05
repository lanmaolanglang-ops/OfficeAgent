"""Repository 基类"""
from typing import TypeVar, Generic, Type, Optional, List, Any, Dict, cast
from sqlalchemy import select, func
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import Executable

from ..base import Base

ModelType = TypeVar("ModelType", bound=Base)

# 分页硬上限：Repository 防御层与 API 输入契约（api/core/pagination.py）
# 共用同一常量，任何端点声明的业务上限不得超过 MAX_LIMIT。
MIN_LIMIT = 1
MAX_LIMIT = 1000


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
        if (isinstance(limit, bool) or not isinstance(limit, int)
                or not MIN_LIMIT <= limit <= MAX_LIMIT):
            raise ValueError(f"limit 必须在 {MIN_LIMIT} 到 {MAX_LIMIT} 之间")
        return offset, limit

    @classmethod
    def _bounded_limit(cls, limit: int) -> int:
        """校验仅带 limit 的查询（无 offset），与 _page 同一规则。"""
        return cls._page(0, limit)[1]

    def _column(self, name: str):
        if not isinstance(name, str) or name not in self.model.__mapper__.attrs:
            raise ValueError(f"未知过滤字段: {name}")
        return getattr(self.model, name)

    def _execute_rowcount(self, statement: Executable) -> int:
        """执行 DML 语句并返回受影响行数。

        SQLAlchemy 2.0 的 ``Session.execute`` 静态返回类型恒为 ``Result[Any]``，
        无法在类型层区分 DML（运行时实际返回 ``CursorResult``，才有 ``rowcount``）。
        此处把该运行时不变式显式化，避免每个调用点重复 cast。
        """
        result = cast("CursorResult[Any]", self.session.execute(statement))
        return result.rowcount

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
