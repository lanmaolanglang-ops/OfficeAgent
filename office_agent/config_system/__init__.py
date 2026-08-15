"""
配置管理系统

统一管理模型、Agent、Prompt、Skill、Workflow 等配置。
支持多来源加载、热更新、缓存。

用法：
    from office_agent.config_system import get_config

    config = get_config()
    model = config.get_model("doubao-pro")
    prompt = config.get_prompt("word_format")
    agent_cfg = config.get_agent("WordAgent")
"""
from .schemas import (
    GlobalConfig, StorageConfig, QueueConfig, LoggingConfig,
    ModelConfigSchema, AgentConfigSchema, PromptConfigSchema,
    SkillConfigSchema, WorkflowConfigSchema, WorkflowStep,
    ModelProvider, PromptStatus,
)
from .config_manager import ConfigManager, get_config
from .loaders import EnvLoader, YamlLoader, DatabaseLoader, deep_merge
from .validators import ConfigValidator, validate_all, Severity

__all__ = [
    # Schemas
    "GlobalConfig", "StorageConfig", "QueueConfig", "LoggingConfig",
    "ModelConfigSchema", "AgentConfigSchema", "PromptConfigSchema",
    "SkillConfigSchema", "WorkflowConfigSchema", "WorkflowStep",
    "ModelProvider", "PromptStatus",
    # Manager
    "ConfigManager", "get_config",
    # Loaders
    "EnvLoader", "YamlLoader", "DatabaseLoader", "deep_merge",
    # Validators
    "ConfigValidator", "validate_all", "Severity",
]
