"""配置管理数据库模型"""
import uuid
from sqlalchemy import String, Text, Integer, Float, Boolean, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin


def _uuid(prefix="cfg"):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ModelConfig(Base, TimestampMixin):
    """LLM 模型配置表"""
    __tablename__ = "model_config"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: _uuid("mdl"))
    model_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    api_endpoint: Mapped[str] = mapped_column(String(512), nullable=True)
    api_key_env: Mapped[str] = mapped_column(String(128), nullable=True)
    api_key_ref: Mapped[str] = mapped_column(String(256), nullable=True)

    # 生成参数
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    top_p: Mapped[float] = mapped_column(Float, default=1.0)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    presence_penalty: Mapped[float] = mapped_column(Float, default=0.0)
    frequency_penalty: Mapped[float] = mapped_column(Float, default=0.0)

    # 能力
    context_length: Mapped[int] = mapped_column(Integer, default=8192)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_streaming: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_function_calling: Mapped[bool] = mapped_column(Boolean, default=False)

    # 成本
    cost_input_per_1k: Mapped[float] = mapped_column(Float, default=0.0)
    cost_output_per_1k: Mapped[float] = mapped_column(Float, default=0.0)

    # 状态
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    description: Mapped[str] = mapped_column(Text, nullable=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")

    def __repr__(self):
        return f"<ModelConfig {self.model_id} ({self.provider})>"


class AgentConfigModel(Base, TimestampMixin):
    """Agent 配置表（运行时配置）"""
    __tablename__ = "agent_runtime_config"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: _uuid("agc"))
    agent_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(16), default="1.0.0")

    # Prompt
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    prompt_template: Mapped[str] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(16), default="latest")

    # 模型
    model_priority: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    fallback_models: Mapped[str] = mapped_column(Text, default="[]")

    # 工具
    available_tools: Mapped[str] = mapped_column(Text, default="[]")  # JSON array

    # 执行
    timeout: Mapped[int] = mapped_column(Integer, default=120)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    retry_config: Mapped[str] = mapped_column(Text, default="{}")

    # 限制
    max_input_length: Mapped[int] = mapped_column(Integer, default=100000)
    max_output_length: Mapped[int] = mapped_column(Integer, default=50000)

    # 质量
    enable_quality_check: Mapped[bool] = mapped_column(Boolean, default=True)
    quality_threshold: Mapped[float] = mapped_column(Float, default=0.7)

    # 状态
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    tags: Mapped[str] = mapped_column(Text, default="[]")
    config_json: Mapped[str] = mapped_column(Text, default="{}")

    def __repr__(self):
        return f"<AgentConfig {self.agent_name} v{self.version}>"


class PromptConfig(Base, TimestampMixin):
    """Prompt 配置表"""
    __tablename__ = "prompt_config"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: _uuid("prm"))
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(16), default="1.0.0")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    # 适用范围
    agent: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=True, index=True)

    # 变量
    variables: Mapped[str] = mapped_column(Text, default="[]")

    # 状态
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    author: Mapped[str] = mapped_column(String(64), nullable=True)
    tags: Mapped[str] = mapped_column(Text, default="[]")
    config_json: Mapped[str] = mapped_column(Text, default="{}")

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_prompt_config_name_version"),
    )

    def __repr__(self):
        return f"<PromptConfig {self.name} v{self.version}>"


class SkillConfigModel(Base, TimestampMixin):
    """Skill 配置表"""
    __tablename__ = "skill_config"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: _uuid("skc"))
    skill_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(16), default="1.0.0")

    workflow: Mapped[str] = mapped_column(Text, default="[]")
    tools: Mapped[str] = mapped_column(Text, default="[]")
    prompt: Mapped[str] = mapped_column(Text, nullable=True)

    trigger_keywords: Mapped[str] = mapped_column(Text, default="[]")
    trigger_patterns: Mapped[str] = mapped_column(Text, default="[]")
    parameters: Mapped[str] = mapped_column(Text, default="{}")

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    tags: Mapped[str] = mapped_column(Text, default="[]")
    config_json: Mapped[str] = mapped_column(Text, default="{}")

    def __repr__(self):
        return f"<SkillConfig {self.skill_name}>"


class WorkflowConfig(Base, TimestampMixin):
    """工作流配置表"""
    __tablename__ = "workflow_config"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: _uuid("wfc"))
    workflow_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(16), default="1.0.0")

    steps: Mapped[str] = mapped_column(Text, default="[]")
    input_schema: Mapped[str] = mapped_column(Text, default="{}")
    output_schema: Mapped[str] = mapped_column(Text, default="{}")

    timeout: Mapped[int] = mapped_column(Integer, default=600)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    tags: Mapped[str] = mapped_column(Text, default="[]")
    config_json: Mapped[str] = mapped_column(Text, default="{}")

    def __repr__(self):
        return f"<WorkflowConfig {self.workflow_name}>"


class FileRuleConfig(Base, TimestampMixin):
    """文件处理规则配置表"""
    __tablename__ = "file_rule_config"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: _uuid("frc"))
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False)
    file_pattern: Mapped[str] = mapped_column(String(256), nullable=False)
    actions: Mapped[str] = mapped_column(Text, default="[]")
    parameters: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")

    def __repr__(self):
        return f"<FileRuleConfig {self.rule_name}>"
