"""PPT 相关数据访问"""
from typing import Optional
from sqlalchemy import select, update, func

from .base import BaseRepository
from ..models.ppt import PPTTemplate, SlideHistory, PPTGeneration


class PPTTemplateRepository(BaseRepository[PPTTemplate]):
    """PPT 模板仓库"""

    def __init__(self, session_factory=None):
        super().__init__(PPTTemplate, session_factory)

    def find_enabled(self) -> list[PPTTemplate]:
        """查找所有启用的模板"""
        with self.get_session() as session:
            stmt = select(PPTTemplate).where(PPTTemplate.enabled == True)
            return list(session.scalars(stmt).all())

    def find_by_style(self, style: str) -> list[PPTTemplate]:
        """按风格查找模板"""
        with self.get_session() as session:
            stmt = select(PPTTemplate).where(
                PPTTemplate.style == style,
                PPTTemplate.enabled == True,
            )
            return list(session.scalars(stmt).all())

    def find_by_scenario(self, scenario: str) -> list[PPTTemplate]:
        """按场景查找模板"""
        with self.get_session() as session:
            stmt = select(PPTTemplate).where(
                PPTTemplate.scenario == scenario,
                PPTTemplate.enabled == True,
            )
            return list(session.scalars(stmt).all())

    def increment_usage(self, template_id: str):
        """增加使用次数"""
        with self.get_session() as session:
            stmt = (
                update(PPTTemplate)
                .where(PPTTemplate.id == template_id)
                .values(usage_count=PPTTemplate.usage_count + 1)
            )
            session.execute(stmt)
            session.commit()


class SlideHistoryRepository(BaseRepository[SlideHistory]):
    """幻灯片历史仓库"""

    def __init__(self, session_factory=None):
        super().__init__(SlideHistory, session_factory)

    def find_by_generation(self, generation_id: str) -> list[SlideHistory]:
        """查找某次生成的所有幻灯片"""
        with self.get_session() as session:
            stmt = (
                select(SlideHistory)
                .where(SlideHistory.generation_id == generation_id)
                .order_by(SlideHistory.slide_index)
            )
            return list(session.scalars(stmt).all())

    def find_by_presentation(self, presentation_id: str) -> list[SlideHistory]:
        """查找某个演示文稿的所有版本"""
        with self.get_session() as session:
            stmt = (
                select(SlideHistory)
                .where(SlideHistory.presentation_id == presentation_id)
                .order_by(SlideHistory.slide_index, SlideHistory.version.desc())
            )
            return list(session.scalars(stmt).all())

    def get_latest_version(self, presentation_id: str, slide_index: int) -> Optional[SlideHistory]:
        """获取某页的最新版本"""
        with self.get_session() as session:
            stmt = (
                select(SlideHistory)
                .where(
                    SlideHistory.presentation_id == presentation_id,
                    SlideHistory.slide_index == slide_index,
                )
                .order_by(SlideHistory.version.desc())
                .limit(1)
            )
            return session.scalar(stmt)


class PPTGenerationRepository(BaseRepository[PPTGeneration]):
    """PPT 生成记录仓库"""

    def __init__(self, session_factory=None):
        super().__init__(PPTGeneration, session_factory)

    def find_by_user(self, user_id: str, limit: int = 20) -> list[PPTGeneration]:
        """查找用户的生成记录"""
        with self.get_session() as session:
            stmt = (
                select(PPTGeneration)
                .where(PPTGeneration.user_id == user_id)
                .order_by(PPTGeneration.created_at.desc())
                .limit(limit)
            )
            return list(session.scalars(stmt).all())

    def find_completed(self) -> list[PPTGeneration]:
        """查找已完成的生成"""
        with self.get_session() as session:
            stmt = select(PPTGeneration).where(PPTGeneration.status == "completed")
            return list(session.scalars(stmt).all())

    def update_status(self, gen_id: str, status: str, **kwargs):
        """更新状态"""
        with self.get_session() as session:
            values = {"status": status}
            values.update(kwargs)
            stmt = update(PPTGeneration).where(PPTGeneration.id == gen_id).values(**values)
            session.execute(stmt)
            session.commit()

    def count_by_user(self, user_id: str) -> int:
        """统计用户生成次数"""
        with self.get_session() as session:
            stmt = select(func.count()).select_from(PPTGeneration).where(
                PPTGeneration.user_id == user_id
            )
            return session.scalar(stmt) or 0
