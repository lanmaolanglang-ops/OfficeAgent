"""
模型网关相关数据结构
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ModelProvider(Enum):
    """模型提供商"""
    DOUBAO = "doubao"           # 豆包/火山引擎
    OPENAI = "openai"           # OpenAI
    CLAUDE = "claude"           # Anthropic Claude
    GEMINI = "gemini"           # Google Gemini
    DEEPSEEK = "deepseek"       # DeepSeek
    QWEN = "qwen"               # 通义千问
    AGNES = "agnes"             # Agnes AI
    CUSTOM = "custom"           # 自定义(OpenAI兼容)


class AITaskType(Enum):
    """AI任务类型（用于路由）"""
    DOCUMENT_UNDERSTANDING = "document_understanding"  # 长文档/论文分析
    CHINESE_WRITING = "chinese_writing"                # 中文内容生成
    CODE_GENERATION = "code_generation"                # 代码生成
    VISION = "vision"                                  # 多模态/图片分析
    SIMPLE_TEXT = "simple_text"                        # 简单文本/参数解析
    FORMULA_GENERATION = "formula_generation"          # Excel公式生成
    PPT_CONTENT = "ppt_content"                        # PPT内容规划


@dataclass
class ModelConfig:
    """模型配置"""
    id: str                                  # 唯一标识
    provider: ModelProvider                  # 提供商
    display_name: str                        # 显示名称
    api_key: str = ""                        # API Key（加密存储）
    base_url: str = ""                       # Base URL
    model: str = ""                          # 模型名称
    enabled: bool = True                     # 是否启用
    priority: int = 100                      # 优先级（数字越小越优先）
    max_tokens: int = 8192                   # 最大token数
    temperature: float = 0.3                 # 温度
    timeout: int = 60                        # 超时时间（秒）
    supports_vision: bool = False            # 是否支持视觉
    supports_document: bool = False          # 是否支持文档上传
    extra_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_key: bool = False) -> dict:
        result = {
            "id": self.id,
            "provider": self.provider.value,
            "display_name": self.display_name,
            "base_url": self.base_url,
            "model": self.model,
            "enabled": self.enabled,
            "priority": self.priority,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "supports_vision": self.supports_vision,
            "supports_document": self.supports_document,
        }
        if include_key:
            result["api_key"] = self.api_key
        return result


@dataclass
class ModelResponse:
    """模型响应"""
    success: bool
    content: str = ""
    model_used: str = ""           # 实际使用的模型ID
    provider: str = ""             # 实际使用的提供商
    tokens_used: int = 0
    latency_ms: int = 0
    error: str = ""
    raw_response: Any = None


@dataclass
class ChatMessage:
    """聊天消息"""
    role: str       # system/user/assistant
    content: str
    name: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        return d


# 默认模型配置模板
DEFAULT_MODEL_CONFIGS = {
    ModelProvider.OPENAI: ModelConfig(
        id="openai-default",
        provider=ModelProvider.OPENAI,
        display_name="OpenAI GPT",
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
        supports_vision=True,
        supports_document=True,
        priority=10,
    ),
    ModelProvider.DEEPSEEK: ModelConfig(
        id="deepseek-default",
        provider=ModelProvider.DEEPSEEK,
        display_name="DeepSeek Chat",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        supports_vision=False,
        supports_document=False,
        priority=50,
    ),
    ModelProvider.AGNES: ModelConfig(
        id="agnes-default",
        provider=ModelProvider.AGNES,
        display_name="Agnes Flash",
        base_url="https://apihub.agnes-ai.com/v1",
        model="agnes-2.5-flash",
        supports_vision=True,
        supports_document=True,
        priority=30,
    ),
    ModelProvider.DOUBAO: ModelConfig(
        id="doubao-default",
        provider=ModelProvider.DOUBAO,
        display_name="豆包 Pro",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-pro-32k",
        supports_vision=False,
        supports_document=False,
        priority=20,
    ),
    ModelProvider.QWEN: ModelConfig(
        id="qwen-default",
        provider=ModelProvider.QWEN,
        display_name="通义千问 Max",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-max",
        supports_vision=False,
        supports_document=False,
        priority=25,
    ),
    ModelProvider.CLAUDE: ModelConfig(
        id="claude-default",
        provider=ModelProvider.CLAUDE,
        display_name="Claude 3.5 Sonnet",
        base_url="https://api.anthropic.com/v1",
        model="claude-3-5-sonnet-20241022",
        supports_vision=True,
        supports_document=True,
        priority=15,
    ),
    ModelProvider.GEMINI: ModelConfig(
        id="gemini-default",
        provider=ModelProvider.GEMINI,
        display_name="Gemini Pro",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-1.5-pro",
        supports_vision=True,
        supports_document=True,
        priority=18,
    ),
}

# 默认路由策略（任务类型 -> 模型ID优先级列表）
DEFAULT_ROUTING = {
    AITaskType.DOCUMENT_UNDERSTANDING: [
        "openai-default", "claude-default", "gemini-default",
        "doubao-default", "deepseek-default",
    ],
    AITaskType.CHINESE_WRITING: [
        "doubao-default", "qwen-default", "deepseek-default",
        "openai-default", "claude-default", "agnes-default",
    ],
    AITaskType.CODE_GENERATION: [
        "openai-default", "deepseek-default", "claude-default",
        "qwen-default", "doubao-default",
    ],
    AITaskType.VISION: [
        "gemini-default", "openai-default", "claude-default",
    ],
    AITaskType.SIMPLE_TEXT: [
        "deepseek-default", "doubao-default", "qwen-default",
    ],
    AITaskType.FORMULA_GENERATION: [
        "openai-default", "claude-default", "deepseek-default",
        "qwen-default", "doubao-default",
    ],
    AITaskType.PPT_CONTENT: [
        "doubao-default", "qwen-default", "openai-default",
        "claude-default", "deepseek-default", "agnes-default",
    ],
}
