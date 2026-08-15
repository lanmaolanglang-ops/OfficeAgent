"""技能 Repository"""
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.orm import Session

from .base import BaseRepository
from ..models.skill import Skill


class SkillRepository(BaseRepository[Skill]):
    def __init__(self, session: Session):
        super().__init__(session, Skill)

    def get_enabled(self) -> List[Skill]:
        return self.find(enabled=True)

    def get_by_category(self, category: str) -> List[Skill]:
        return self.find(category=category)

    def create_skill(self, name: str, skill_type: str = None,
                     description: str = None, prompt: str = None,
                     version: str = "1.0.0", category: str = None,
                     tags: str = None, config_json: str = None) -> Skill:
        skill = Skill(
            name=name, skill_type=skill_type, description=description,
            prompt=prompt, version=version, category=category,
            tags=tags, config_json=config_json,
        )
        return self.create(skill)

    def increment_usage(self, skill_id: str):
        skill = self.get_by_id(skill_id)
        if skill:
            self.update(skill_id, {"usage_count": skill.usage_count + 1})
