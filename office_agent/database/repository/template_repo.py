"""模板 Repository"""
from typing import Optional, List
from sqlalchemy.orm import Session

from .base import BaseRepository
from ..models.template import Template


class TemplateRepository(BaseRepository[Template]):
    def __init__(self, session: Session):
        super().__init__(session, Template)

    def get_by_type(self, template_type: str) -> List[Template]:
        return self.find(template_type=template_type)

    def get_by_category(self, category: str) -> List[Template]:
        return self.find(category=category)

    def get_builtin(self) -> List[Template]:
        return self.find(is_builtin=True)

    def create_template(self, name: str, template_type: str,
                        file_path: str = None, config_json: str = None,
                        description: str = None, category: str = None,
                        tags: str = None, is_builtin: bool = False) -> Template:
        tpl = Template(
            name=name, template_type=template_type, file_path=file_path,
            config_json=config_json, description=description,
            category=category, tags=tags, is_builtin=is_builtin,
        )
        return self.create(tpl)

    def increment_usage(self, template_id: str):
        tpl = self.get_by_id(template_id)
        if tpl:
            self.update(template_id, {"usage_count": tpl.usage_count + 1})
