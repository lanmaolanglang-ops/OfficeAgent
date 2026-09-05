"""Agent配置 Repository"""
from typing import Optional, List
from sqlalchemy.orm import Session

from .base import BaseRepository
from ..models.agent import AgentConfig


class AgentRepository(BaseRepository[AgentConfig]):
    def __init__(self, session: Session):
        super().__init__(session, AgentConfig)

    def get_by_agent_id(self, agent_id: str) -> Optional[AgentConfig]:
        return self.find_one(agent_id=agent_id)

    def get_enabled(self) -> List[AgentConfig]:
        return self.find(enabled=True)

    def get_by_type(self, agent_type: str) -> List[AgentConfig]:
        return self.find(agent_type=agent_type)

    def create_agent(self, agent_id: str, name: str, agent_type: str,
                     description: str | None = None, version: str = "1.0.0",
                     capabilities: str | None = None, model_config: str | None = None,
                     prompt_template: str | None = None, config_json: str | None = None) -> AgentConfig:
        agent = AgentConfig(
            agent_id=agent_id,
            name=name,
            agent_type=agent_type,
            description=description,
            version=version,
            capabilities=capabilities,
            model_config=model_config,
            prompt_template=prompt_template,
            config_json=config_json,
        )
        return self.create(agent)

    def update_config(self, agent_id: str, config_json: str):
        agent = self.get_by_agent_id(agent_id)
        if agent:
            self.update(agent.id, {"config_json": config_json})
        return agent

    def update_model_config(self, agent_id: str, model_config: str):
        agent = self.get_by_agent_id(agent_id)
        if agent:
            self.update(agent.id, {"model_config": model_config})
        return agent

    def enable(self, agent_id: str):
        agent = self.get_by_agent_id(agent_id)
        if agent:
            self.update(agent.id, {"enabled": True})
        return agent

    def disable(self, agent_id: str):
        agent = self.get_by_agent_id(agent_id)
        if agent:
            self.update(agent.id, {"enabled": False})
        return agent
