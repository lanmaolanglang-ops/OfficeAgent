"""模板 Repository"""
from typing import List
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
                        file_path: str | None = None, config_json: str | None = None,
                        description: str | None = None, category: str | None = None,
                        tags: str | None = None, is_builtin: bool = False) -> Template:
        tpl = Template(
            name=name, template_type=template_type, file_path=file_path,
            config_json=config_json, description=description,
            category=category, tags=tags, is_builtin=is_builtin,
        )
        return self.create(tpl)

    def increment_usage(self, template_id: str):
        from sqlalchemy import update
        return self._execute_rowcount(
            update(Template).where(Template.id == template_id)
            .values(usage_count=Template.usage_count + 1)
            .execution_options(synchronize_session="fetch")
        )
