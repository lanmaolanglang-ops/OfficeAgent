"""技能 Repository"""
from typing import List
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .base import BaseRepository
from ..models.skill import Skill


class SkillRepository(BaseRepository[Skill]):
    def __init__(self, session: Session):
        super().__init__(session, Skill)

    def get_enabled(self, owner_id: str | None = None) -> List[Skill]:
        stmt = select(Skill).where(Skill.enabled.is_(True))
        if owner_id is None:
            stmt = stmt.where(Skill.owner_id.is_(None))
        else:
            stmt = stmt.where(or_(Skill.owner_id == owner_id, Skill.owner_id.is_(None)))
        stmt = stmt.order_by(Skill.priority.asc(), Skill.created_at.asc(), Skill.id.asc())
        return list(self.session.scalars(stmt))

    def list_for_owner(self, owner_id: str | None = None) -> List[Skill]:
        stmt = select(Skill)
        if owner_id is None:
            stmt = stmt.where(Skill.owner_id.is_(None))
        else:
            stmt = stmt.where(Skill.owner_id == owner_id)
        return list(self.session.scalars(
            stmt.order_by(Skill.priority.asc(), Skill.created_at.asc(), Skill.id.asc())
        ))

    def get_owned(self, skill_id: str, owner_id: str | None = None) -> Skill | None:
        skill = self.get_by_id(skill_id)
        if not skill or skill.owner_id != owner_id:
            return None
        return skill

    def get_by_category(self, category: str) -> List[Skill]:
        return self.find(category=category)

    def create_skill(self, name: str, skill_type: str | None = None,
                     description: str | None = None, prompt: str | None = None,
                     version: str = "1.0.0", category: str | None = None,
                     tags: str | None = None, config_json: str | None = None,
                     target_agents: str = '["all"]', priority: int = 100,
                     source: str = "ui", owner_id: str | None = None) -> Skill:
        skill = Skill(
            name=name, skill_type=skill_type, description=description,
            prompt=prompt, version=version, category=category,
            tags=tags, config_json=config_json, target_agents=target_agents,
            priority=priority, source=source, owner_id=owner_id,
        )
        return self.create(skill)

    def increment_usage(self, skill_id: str):
        from sqlalchemy import update
        return self._execute_rowcount(
            update(Skill).where(Skill.id == skill_id)
            .values(usage_count=Skill.usage_count + 1)
            .execution_options(synchronize_session="fetch")
        )
