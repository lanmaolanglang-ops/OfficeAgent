"""Excel 相关数据访问"""
from typing import Optional
from sqlalchemy import select, update, func

from .base import BaseRepository
from ..models.excel import ExcelTemplate, AnalysisHistory, ExcelGeneration


class ExcelTemplateRepository(BaseRepository[ExcelTemplate]):
    """Excel 模板仓库"""

    def __init__(self, session_factory=None):
        super().__init__(ExcelTemplate, session_factory)

    def find_enabled(self) -> list[ExcelTemplate]:
        """查找所有启用的模板"""
        with self.get_session() as session:
            stmt = select(ExcelTemplate).where(ExcelTemplate.enabled == True)
            return list(session.scalars(stmt).all())

    def find_by_type(self, template_type: str) -> list[ExcelTemplate]:
        """按类型查找模板"""
        with self.get_session() as session:
            stmt = select(ExcelTemplate).where(
                ExcelTemplate.type == template_type,
                ExcelTemplate.enabled == True,
            )
            return list(session.scalars(stmt).all())

    def find_by_theme(self, theme: str) -> list[ExcelTemplate]:
        """按主题查找模板"""
        with self.get_session() as session:
            stmt = select(ExcelTemplate).where(
                ExcelTemplate.theme == theme,
                ExcelTemplate.enabled == True,
            )
            return list(session.scalars(stmt).all())

    def increment_usage(self, template_id: str):
        """增加使用次数"""
        with self.get_session() as session:
            stmt = (
                update(ExcelTemplate)
                .where(ExcelTemplate.id == template_id)
                .values(usage_count=ExcelTemplate.usage_count + 1)
            )
            session.execute(stmt)
            session.commit()


class AnalysisHistoryRepository(BaseRepository[AnalysisHistory]):
    """分析历史仓库"""

    def __init__(self, session_factory=None):
        super().__init__(AnalysisHistory, session_factory)

    def find_by_task(self, task_id: str) -> list[AnalysisHistory]:
        """查找某任务的所有分析记录"""
        with self.get_session() as session:
            stmt = (
                select(AnalysisHistory)
                .where(AnalysisHistory.task_id == task_id)
                .order_by(AnalysisHistory.created_at.desc())
            )
            return list(session.scalars(stmt).all())

    def find_by_file(self, file_id: str) -> list[AnalysisHistory]:
        """查找某文件的分析记录"""
        with self.get_session() as session:
            stmt = (
                select(AnalysisHistory)
                .where(AnalysisHistory.file_id == file_id)
                .order_by(AnalysisHistory.created_at.desc())
            )
            return list(session.scalars(stmt).all())

    def find_by_type(self, analysis_type: str, limit: int = 50) -> list[AnalysisHistory]:
        """按分析类型查找"""
        with self.get_session() as session:
            stmt = (
                select(AnalysisHistory)
                .where(AnalysisHistory.analysis_type == analysis_type)
                .order_by(AnalysisHistory.created_at.desc())
                .limit(limit)
            )
            return list(session.scalars(stmt).all())

    def find_by_user(self, user_id: str, limit: int = 20) -> list[AnalysisHistory]:
        """查找用户的分析记录"""
        with self.get_session() as session:
            stmt = (
                select(AnalysisHistory)
                .where(AnalysisHistory.user_id == user_id)
                .order_by(AnalysisHistory.created_at.desc())
                .limit(limit)
            )
            return list(session.scalars(stmt).all())

    def count_by_type(self, analysis_type: Optional[str] = None) -> int:
        """统计分析次数"""
        with self.get_session() as session:
            stmt = select(func.count()).select_from(AnalysisHistory)
            if analysis_type:
                stmt = stmt.where(AnalysisHistory.analysis_type == analysis_type)
            return session.scalar(stmt) or 0


class ExcelGenerationRepository(BaseRepository[ExcelGeneration]):
    """Excel 生成记录仓库"""

    def __init__(self, session_factory=None):
        super().__init__(ExcelGeneration, session_factory)

    def find_by_user(self, user_id: str, limit: int = 20) -> list[ExcelGeneration]:
        """查找用户的生成记录"""
        with self.get_session() as session:
            stmt = (
                select(ExcelGeneration)
                .where(ExcelGeneration.user_id == user_id)
                .order_by(ExcelGeneration.created_at.desc())
                .limit(limit)
            )
            return list(session.scalars(stmt).all())

    def find_by_task_type(self, task_type: str) -> list[ExcelGeneration]:
        """按任务类型查找"""
        with self.get_session() as session:
            stmt = select(ExcelGeneration).where(ExcelGeneration.task_type == task_type)
            return list(session.scalars(stmt).all())

    def update_status(self, gen_id: str, status: str, **kwargs):
        """更新状态"""
        with self.get_session() as session:
            values = {"status": status}
            values.update(kwargs)
            stmt = update(ExcelGeneration).where(ExcelGeneration.id == gen_id).values(**values)
            session.execute(stmt)
            session.commit()

    def count_by_user(self, user_id: str) -> int:
        """统计用户处理次数"""
        with self.get_session() as session:
            stmt = select(func.count()).select_from(ExcelGeneration).where(
                ExcelGeneration.user_id == user_id
            )
            return session.scalar(stmt) or 0
