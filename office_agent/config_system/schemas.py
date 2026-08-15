"""
配置数据模型（Pydantic）

定义所有配置项的结构、默认值和校验规则。
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator
from enum import Enum


# ============================================================
# 模型配置
# ============================================================

class ModelProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DOUBAO = "doubao"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    OLLAMA = "ollama"
    CUSTOM = "custom"


class ModelConfigSchema(BaseModel):
    """LLM 模型配置"""
    model_id: str = Field(..., description="模型唯一标识")
    model_name: str = Field(..., description="模型名称")
    provider: ModelProvider = Field(..., description="提供商")
    api_endpoint: Optional[str] = Field(None, description="API 端点")
    api_key_env: Optional[str] = Field(None, description="API Key 环境变量名")
    api_key_reference: Optional[str] = Field(None, description="API Key 引用（不存明文）")

    # 生成参数
    temperature: float = Field(0.7, ge=0, le=2)
    top_p: float = Field(1.0, ge=0, le=1)
    max_tokens: int = Field(4096, ge=1, le=128000)
    presence_penalty: float = Field(0.0, ge=-2, le=2)
    frequency_penalty: float = Field(0.0, ge=-2, le=2)

    # 能力
    context_length: int = Field(8192, description="上下文窗口大小")
    supports_vision: bool = False
    supports_streaming: bool = True
    supports_function_calling: bool = False

    # 成本（每 1K tokens，美元）
    cost_input_per_1k: float = 0.0
    cost_output_per_1k: float = 0.0

    # 状态
    enabled: bool = True
    priority: int = Field(0, description="优先级，数字越大越优先")
    tags: List[str] = Field(default_factory=list)

    # 元数据
    description: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


# ============================================================
# Agent 配置
# ============================================================

class ToolConfig(BaseModel):
    """工具配置"""
    name: str
    enabled: bool = True
    parameters: Dict[str, Any] = Field(default_factory=dict)


class RetryConfig(BaseModel):
    """重试配置"""
    max_retries: int = Field(3, ge=0, le=10)
    retry_delay: float = Field(1.0, ge=0)
    backoff_factor: float = Field(2.0, ge=1)
    retry_on: List[str] = Field(default_factory=lambda: ["timeout", "rate_limit", "server_error"])


class AgentConfigSchema(BaseModel):
    """Agent 配置"""
    agent_name: str = Field(..., description="Agent 名称")
    description: str = ""
    version: str = "1.0.0"

    # Prompt
    system_prompt: str = ""
    prompt_template: Optional[str] = None
    prompt_version: str = "latest"

    # 模型
    model_priority: List[str] = Field(default_factory=list, description="模型优先级列表")
    fallback_models: List[str] = Field(default_factory=list)

    # 工具
    available_tools: List[ToolConfig] = Field(default_factory=list)

    # 执行
    timeout: int = Field(120, ge=1, le=3600, description="超时秒数")
    max_retries: int = Field(3, ge=0)
    retry: RetryConfig = Field(default_factory=RetryConfig)

    # 限制
    max_input_length: int = Field(100000)
    max_output_length: int = Field(50000)

    # 质量
    enable_quality_check: bool = True
    quality_threshold: float = Field(0.7, ge=0, le=1)

    # 状态
    enabled: bool = True
    tags: List[str] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)


# ============================================================
# Prompt 配置
# ============================================================

class PromptStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class PromptConfigSchema(BaseModel):
    """Prompt 配置"""
    name: str = Field(..., description="Prompt 名称")
    version: str = Field("1.0.0", description="版本号")
    content: str = Field(..., description="Prompt 内容")
    description: str = ""

    # 适用范围
    agent: Optional[str] = Field(None, description="适用 Agent")
    task_type: Optional[str] = Field(None, description="适用任务类型")

    # 变量
    variables: List[str] = Field(default_factory=list, description="模板变量列表")

    # 元数据
    status: PromptStatus = PromptStatus.ACTIVE
    is_default: bool = False
    author: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)


# ============================================================
# Skill 配置
# ============================================================

class SkillConfigSchema(BaseModel):
    """Skill 配置"""
    skill_name: str
    description: str = ""
    version: str = "1.0.0"

    # 工作流
    workflow: List[Dict[str, Any]] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    prompt: Optional[str] = None

    # 触发
    trigger_keywords: List[str] = Field(default_factory=list)
    trigger_patterns: List[str] = Field(default_factory=list)

    # 参数
    parameters: Dict[str, Any] = Field(default_factory=dict)

    # 状态
    enabled: bool = True
    tags: List[str] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)


# ============================================================
# Workflow 配置
# ============================================================

class WorkflowStep(BaseModel):
    """工作流步骤"""
    step_id: str
    name: str
    agent: Optional[str] = None
    action: Optional[str] = None
    tool: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    condition: Optional[str] = None
    on_error: str = Field("stop", description="stop/retry/skip/continue")
    timeout: Optional[int] = None
    retry_count: int = 0


class WorkflowConfigSchema(BaseModel):
    """工作流配置"""
    workflow_name: str
    description: str = ""
    version: str = "1.0.0"

    steps: List[WorkflowStep] = Field(default_factory=list)

    # 参数定义
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)

    # 执行
    timeout: int = 600
    max_concurrency: int = 1

    # 状态
    enabled: bool = True
    tags: List[str] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)


# ============================================================
# 文件规则配置
# ============================================================

class FileRuleSchema(BaseModel):
    """文件处理规则"""
    rule_name: str
    file_pattern: str = Field(..., description="文件匹配模式，如 *.docx")
    actions: List[str] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


# ============================================================
# 全局配置
# ============================================================

class StorageConfig(BaseModel):
    """存储配置"""
    storage_type: str = "local"
    local_path: str = "~/.office_agent/storage"
    max_file_size: int = 100 * 1024 * 1024
    max_versions: int = 10
    temp_expire_hours: int = 24


class QueueConfig(BaseModel):
    """队列配置"""
    mode: str = "local"
    broker_url: Optional[str] = None
    result_backend: Optional[str] = None
    max_workers: int = 4
    task_timeout: int = 300


class LoggingConfig(BaseModel):
    """日志配置"""
    level: str = "INFO"
    dir: str = "~/.office_agent/logs"
    enable_db_logging: bool = True
    enable_metrics: bool = True
    max_size_mb: int = 50
    backup_count: int = 10


class GlobalConfig(BaseModel):
    """全局配置"""
    # 基础
    environment: str = "development"
    debug: bool = True
    service_name: str = "office-agent"

    # 存储
    storage: StorageConfig = Field(default_factory=StorageConfig)

    # 队列
    queue: QueueConfig = Field(default_factory=QueueConfig)

    # 日志
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # 默认模型
    default_model: str = "doubao-pro"
    default_vision_model: Optional[str] = None

    # Agent 默认配置
    agent_default_timeout: int = 120
    agent_max_retries: int = 3

    # 功能开关
    enable_rag: bool = True
    enable_quality_check: bool = True
    enable_auto_suggest: bool = True
    enable_multimodal: bool = True

    # 元数据
    version: str = "0.40.0"
    extra: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v):
        if v not in ("development", "testing", "staging", "production"):
            raise ValueError(f"Invalid environment: {v}")
        return v

    @field_validator("logging")
    @classmethod
    def validate_log_level(cls, v):
        if v.level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError(f"Invalid log level: {v.level}")
        return v
