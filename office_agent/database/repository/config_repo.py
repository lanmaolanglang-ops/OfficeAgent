"""配置管理 Repository"""
import json
from typing import Optional, List, Dict, Any
from sqlalchemy import select, and_

from .base import BaseRepository
from ..models.config import (
    ModelConfig, AgentConfigModel, PromptConfig,
    SkillConfigModel, WorkflowConfig, FileRuleConfig,
)


def _loads(s, default=None):
    if not s:
        return default if default is not None else {}
    try:
        return json.loads(s)
    except Exception:
        return default if default is not None else {}


class ModelConfigRepository(BaseRepository[ModelConfig]):
    def __init__(self, session):
        super().__init__(session, ModelConfig)

    def get_by_model_id(self, model_id: str) -> Optional[ModelConfig]:
        return self.session.execute(
            select(ModelConfig).where(ModelConfig.model_id == model_id)
        ).scalar_one_or_none()

    def get_enabled(self) -> List[ModelConfig]:
        return list(self.session.execute(
            select(ModelConfig).where(ModelConfig.enabled == True)
            .order_by(ModelConfig.priority.desc())
        ).scalars().all())

    def get_by_provider(self, provider: str) -> List[ModelConfig]:
        return list(self.session.execute(
            select(ModelConfig).where(
                and_(ModelConfig.provider == provider, ModelConfig.enabled == True)
            )
        ).scalars().all())

    def upsert(self, model_id: str, data: dict) -> ModelConfig:
        existing = self.get_by_model_id(model_id)
        if existing:
            for k, v in data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
            self.session.commit()
            return existing
        config = ModelConfig(model_id=model_id, **data)
        return self.create(config)

    def to_dict(self, m: ModelConfig) -> dict:
        return {
            "id": m.id, "model_id": m.model_id, "model_name": m.model_name,
            "provider": m.provider, "api_endpoint": m.api_endpoint,
            "api_key_env": m.api_key_env,
            "temperature": m.temperature, "top_p": m.top_p,
            "max_tokens": m.max_tokens,
            "presence_penalty": m.presence_penalty,
            "frequency_penalty": m.frequency_penalty,
            "context_length": m.context_length,
            "supports_vision": m.supports_vision,
            "supports_streaming": m.supports_streaming,
            "supports_function_calling": m.supports_function_calling,
            "cost_input_per_1k": m.cost_input_per_1k,
            "cost_output_per_1k": m.cost_output_per_1k,
            "enabled": m.enabled, "priority": m.priority,
            "tags": _loads(m.tags, []),
            "description": m.description,
            "extra": _loads(m.config_json),
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }


class AgentConfigRepository(BaseRepository[AgentConfigModel]):
    def __init__(self, session):
        super().__init__(session, AgentConfigModel)

    def get_by_name(self, agent_name: str) -> Optional[AgentConfigModel]:
        return self.session.execute(
            select(AgentConfigModel).where(AgentConfigModel.agent_name == agent_name)
        ).scalar_one_or_none()

    def get_enabled(self) -> List[AgentConfigModel]:
        return list(self.session.execute(
            select(AgentConfigModel).where(AgentConfigModel.enabled == True)
        ).scalars().all())

    def upsert(self, agent_name: str, data: dict) -> AgentConfigModel:
        existing = self.get_by_name(agent_name)
        if existing:
            for k, v in data.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
            self.session.commit()
            return existing
        config = AgentConfigModel(agent_name=agent_name, **data)
        return self.create(config)

    def to_dict(self, a: AgentConfigModel) -> dict:
        return {
            "id": a.id, "agent_name": a.agent_name,
            "description": a.description, "version": a.version,
            "system_prompt": a.system_prompt,
            "prompt_template": a.prompt_template,
            "prompt_version": a.prompt_version,
            "model_priority": _loads(a.model_priority, []),
            "fallback_models": _loads(a.fallback_models, []),
            "available_tools": _loads(a.available_tools, []),
            "timeout": a.timeout, "max_retries": a.max_retries,
            "retry_config": _loads(a.retry_config),
            "max_input_length": a.max_input_length,
            "max_output_length": a.max_output_length,
            "enable_quality_check": a.enable_quality_check,
            "quality_threshold": a.quality_threshold,
            "enabled": a.enabled,
            "tags": _loads(a.tags, []),
            "extra": _loads(a.config_json),
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        }


class PromptConfigRepository(BaseRepository[PromptConfig]):
    def __init__(self, session):
        super().__init__(session, PromptConfig)

    def get_by_name_version(self, name: str, version: str = None) -> Optional[PromptConfig]:
        stmt = select(PromptConfig).where(PromptConfig.name == name)
        if version:
            stmt = stmt.where(PromptConfig.version == version)
        else:
            stmt = stmt.where(PromptConfig.is_default == True)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_agent(self, agent: str) -> List[PromptConfig]:
        return list(self.session.execute(
            select(PromptConfig).where(
                and_(PromptConfig.agent == agent, PromptConfig.status == "active")
            )
        ).scalars().all())

    def get_active(self) -> List[PromptConfig]:
        return list(self.session.execute(
            select(PromptConfig).where(PromptConfig.status == "active")
        ).scalars().all())

    def list_versions(self, name: str) -> List[PromptConfig]:
        return list(self.session.execute(
            select(PromptConfig).where(PromptConfig.name == name)
            .order_by(PromptConfig.version.desc())
        ).scalars().all())

    def set_default(self, prompt_id: str):
        """设置某个版本为默认，同时取消其他版本的默认"""
        prompt = self.get_by_id(prompt_id)
        if not prompt:
            return
        # 取消同名其他版本的默认
        others = self.session.execute(
            select(PromptConfig).where(PromptConfig.name == prompt.name)
        ).scalars().all()
        for o in others:
            o.is_default = (o.id == prompt_id)
        self.session.commit()

    def to_dict(self, p: PromptConfig) -> dict:
        return {
            "id": p.id, "name": p.name, "version": p.version,
            "content": p.content, "description": p.description,
            "agent": p.agent, "task_type": p.task_type,
            "variables": _loads(p.variables, []),
            "status": p.status, "is_default": p.is_default,
            "author": p.author, "tags": _loads(p.tags, []),
            "extra": _loads(p.config_json),
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }


class SkillConfigRepository(BaseRepository[SkillConfigModel]):
    def __init__(self, session):
        super().__init__(session, SkillConfigModel)

    def get_by_name(self, name: str) -> Optional[SkillConfigModel]:
        return self.session.execute(
            select(SkillConfigModel).where(SkillConfigModel.skill_name == name)
        ).scalar_one_or_none()

    def get_enabled(self) -> List[SkillConfigModel]:
        return list(self.session.execute(
            select(SkillConfigModel).where(SkillConfigModel.enabled == True)
        ).scalars().all())

    def to_dict(self, s: SkillConfigModel) -> dict:
        return {
            "id": s.id, "skill_name": s.skill_name,
            "description": s.description, "version": s.version,
            "workflow": _loads(s.workflow, []),
            "tools": _loads(s.tools, []),
            "prompt": s.prompt,
            "trigger_keywords": _loads(s.trigger_keywords, []),
            "trigger_patterns": _loads(s.trigger_patterns, []),
            "parameters": _loads(s.parameters),
            "enabled": s.enabled, "tags": _loads(s.tags, []),
            "extra": _loads(s.config_json),
        }


class WorkflowConfigRepository(BaseRepository[WorkflowConfig]):
    def __init__(self, session):
        super().__init__(session, WorkflowConfig)

    def get_by_name(self, name: str) -> Optional[WorkflowConfig]:
        return self.session.execute(
            select(WorkflowConfig).where(WorkflowConfig.workflow_name == name)
        ).scalar_one_or_none()

    def get_enabled(self) -> List[WorkflowConfig]:
        return list(self.session.execute(
            select(WorkflowConfig).where(WorkflowConfig.enabled == True)
        ).scalars().all())

    def to_dict(self, w: WorkflowConfig) -> dict:
        return {
            "id": w.id, "workflow_name": w.workflow_name,
            "description": w.description, "version": w.version,
            "steps": _loads(w.steps, []),
            "input_schema": _loads(w.input_schema),
            "output_schema": _loads(w.output_schema),
            "timeout": w.timeout, "max_concurrency": w.max_concurrency,
            "enabled": w.enabled, "tags": _loads(w.tags, []),
            "extra": _loads(w.config_json),
        }


class FileRuleRepository(BaseRepository[FileRuleConfig]):
    def __init__(self, session):
        super().__init__(session, FileRuleConfig)

    def get_enabled(self) -> List[FileRuleConfig]:
        return list(self.session.execute(
            select(FileRuleConfig).where(FileRuleConfig.enabled == True)
        ).scalars().all())

    def to_dict(self, f: FileRuleConfig) -> dict:
        return {
            "id": f.id, "rule_name": f.rule_name,
            "file_pattern": f.file_pattern,
            "actions": _loads(f.actions, []),
            "parameters": _loads(f.parameters),
            "enabled": f.enabled,
        }
