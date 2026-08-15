"""
Model Manager - 本地模型管理中心
管理模型供应商、默认模型、Fallback链、参数配置
"""
import os
import time
from typing import Optional
from dataclasses import dataclass, field
from enum import Enum


class ModelProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    DOUBAO = "doubao"
    QWEN = "qwen"
    ZHIPU = "zhipu"
    MOONSHOT = "moonshot"
    OLLAMA = "ollama"
    CUSTOM = "custom"


@dataclass
class ProviderInfo:
    """供应商信息"""
    provider: str
    name: str
    base_url: str
    default_model: str
    available_models: list[str]
    has_api_key: bool = False
    enabled: bool = True
    supports_vision: bool = False
    supports_streaming: bool = True


@dataclass
class ModelConfig:
    """模型配置"""
    id: Optional[int] = None
    provider: str = ""
    model_name: str = ""
    display_name: str = ""
    api_key_encrypted: str = ""
    base_url: str = ""
    enabled: bool = True
    is_default: bool = False
    is_fallback: bool = False
    priority: int = 0
    max_tokens: int = 4096
    temperature: float = 0.7
    top_p: float = 1.0
    timeout_seconds: int = 60
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0


# 预设供应商配置
PROVIDER_PRESETS: dict[str, ProviderInfo] = {
    ModelProvider.OPENAI.value: ProviderInfo(
        provider="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o",
        available_models=["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
        supports_vision=True,
    ),
    ModelProvider.ANTHROPIC.value: ProviderInfo(
        provider="anthropic",
        name="Anthropic Claude",
        base_url="https://api.anthropic.com",
        default_model="claude-3-5-sonnet-20241022",
        available_models=["claude-3-5-sonnet-20241022", "claude-3-opus-20240229", "claude-3-haiku-20240307"],
        supports_vision=True,
    ),
    ModelProvider.DEEPSEEK.value: ProviderInfo(
        provider="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        available_models=["deepseek-chat", "deepseek-coder"],
    ),
    ModelProvider.DOUBAO.value: ProviderInfo(
        provider="doubao",
        name="豆包 (Doubao)",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        default_model="doubao-pro-32k",
        available_models=["doubao-pro-32k", "doubao-pro-128k", "doubao-lite-32k"],
        supports_vision=True,
    ),
    ModelProvider.QWEN.value: ProviderInfo(
        provider="qwen",
        name="通义千问 (Qwen)",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-turbo",
        available_models=["qwen-turbo", "qwen-plus", "qwen-max", "qwen-vl-max"],
        supports_vision=True,
    ),
    ModelProvider.ZHIPU.value: ProviderInfo(
        provider="zhipu",
        name="智谱 (GLM)",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4",
        available_models=["glm-4", "glm-4-flash", "glm-4v"],
        supports_vision=True,
    ),
    ModelProvider.MOONSHOT.value: ProviderInfo(
        provider="moonshot",
        name="Moonshot (Kimi)",
        base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
        available_models=["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    ),
    ModelProvider.OLLAMA.value: ProviderInfo(
        provider="ollama",
        name="Ollama (本地)",
        base_url="http://localhost:11434/v1",
        default_model="llama3.1",
        available_models=["llama3.1", "qwen2", "mistral", "codellama"],
    ),
}


class ModelManager:
    """
    模型管理器
    - 管理模型供应商配置
    - 默认模型和Fallback链
    - 模型参数（温度、max_tokens等）
    - 从Credential Manager获取API Key
    """

    def __init__(self, db=None, credential_manager=None):
        self._db = db
        self._credential = credential_manager
        self._cache: dict[str, ModelConfig] = {}
        self._load_from_db()

    def _load_from_db(self) -> None:
        if self._db:
            try:
                configs = self._db.get_model_configs()
                for c in configs:
                    self._cache[f"{c['provider']}/{c['model_name']}"] = ModelConfig(
                        id=c["id"], provider=c["provider"], model_name=c["model_name"],
                        base_url=c.get("base_url", ""), enabled=bool(c.get("enabled", 1)),
                        is_default=bool(c.get("is_default", 0)),
                        is_fallback=bool(c.get("is_fallback", 0)),
                        priority=c.get("priority", 0), max_tokens=c.get("max_tokens", 4096),
                        temperature=c.get("temperature", 0.7),
                    )
            except Exception:
                pass

    def get_provider_info(self, provider: str) -> Optional[ProviderInfo]:
        info = PROVIDER_PRESETS.get(provider)
        if info and self._credential:
            info.has_api_key = self._credential.has_credential(provider)
        return info

    def list_providers(self) -> list[ProviderInfo]:
        result = []
        for info in PROVIDER_PRESETS.values():
            if self._credential:
                info.has_api_key = self._credential.has_credential(info.provider)
            result.append(info)
        return result

    def get_available_models(self, provider: str = None) -> list[dict]:
        """获取可用模型列表"""
        result = []
        if provider:
            info = self.get_provider_info(provider)
            if info:
                for model in info.available_models:
                    result.append({
                        "provider": provider,
                        "model_name": model,
                        "display_name": f"{info.name} - {model}",
                        "configured": self._cache.get(f"{provider}/{model}") is not None,
                    })
        else:
            for prov, info in PROVIDER_PRESETS.items():
                for model in info.available_models:
                    result.append({
                        "provider": prov,
                        "model_name": model,
                        "display_name": f"{info.name} - {model}",
                        "configured": self._cache.get(f"{prov}/{model}") is not None,
                    })
        return result

    def set_api_key(self, provider: str, api_key: str) -> None:
        """设置供应商API Key"""
        if self._credential:
            self._credential.set_credential(provider, "api_key", api_key)

    def get_api_key(self, provider: str) -> Optional[str]:
        """获取供应商API Key"""
        if self._credential:
            return self._credential.get_credential(provider)
        return os.environ.get(f"{provider.upper()}_API_KEY")

    def enable_model(self, provider: str, model_name: str, config: dict = None) -> ModelConfig:
        """启用模型"""
        config = config or {}
        preset = PROVIDER_PRESETS.get(provider)
        base_url = config.get("base_url", preset.base_url if preset else "")
        mc = ModelConfig(
            provider=provider,
            model_name=model_name,
            display_name=config.get("display_name", f"{provider}/{model_name}"),
            base_url=base_url,
            enabled=True,
            is_default=config.get("is_default", False),
            is_fallback=config.get("is_fallback", False),
            priority=config.get("priority", 0),
            max_tokens=config.get("max_tokens", 4096),
            temperature=config.get("temperature", 0.7),
        )
        # 如果设为默认，取消其他默认
        if mc.is_default:
            for key, existing in self._cache.items():
                existing.is_default = False
        # 保存到数据库
        if self._db:
            existing_id = None
            for k, v in self._cache.items():
                if v.provider == provider and v.model_name == model_name:
                    existing_id = v.id
                    break
            mc.id = self._db.save_model_config({
                "id": existing_id,
                "provider": provider,
                "model_name": model_name,
                "base_url": base_url,
                "enabled": 1,
                "is_default": 1 if mc.is_default else 0,
                "is_fallback": 1 if mc.is_fallback else 0,
                "priority": mc.priority,
                "max_tokens": mc.max_tokens,
                "temperature": mc.temperature,
            })
        self._cache[f"{provider}/{model_name}"] = mc
        return mc

    def disable_model(self, provider: str, model_name: str) -> bool:
        key = f"{provider}/{model_name}"
        if key in self._cache:
            self._cache[key].enabled = False
            if self._db:
                self._db.save_model_config({
                    "id": self._cache[key].id,
                    "provider": provider,
                    "model_name": model_name,
                    "enabled": 0,
                })
            return True
        return False

    def set_default_model(self, provider: str, model_name: str) -> bool:
        """设置默认模型"""
        key = f"{provider}/{model_name}"
        if key not in self._cache:
            self.enable_model(provider, model_name, {"is_default": True})
            return True
        for k, existing in self._cache.items():
            existing.is_default = False
        self._cache[key].is_default = True
        if self._db:
            self._db.save_model_config({
                "id": self._cache[key].id,
                "provider": provider,
                "model_name": model_name,
                "is_default": 1,
            })
        return True

    def get_default_model(self) -> Optional[ModelConfig]:
        """获取默认模型"""
        for mc in self._cache.values():
            if mc.is_default and mc.enabled:
                return mc
        # 如果没有设置默认，返回第一个启用的
        for mc in self._cache.values():
            if mc.enabled:
                return mc
        return None

    def get_fallback_chain(self) -> list[ModelConfig]:
        """获取Fallback链（按优先级排序）"""
        models = [mc for mc in self._cache.values() if mc.enabled]
        # 默认模型优先，然后是fallback，按priority排序
        default = [m for m in models if m.is_default]
        fallbacks = sorted([m for m in models if m.is_fallback and not m.is_default],
                          key=lambda x: x.priority, reverse=True)
        others = sorted([m for m in models if not m.is_default and not m.is_fallback],
                       key=lambda x: x.priority, reverse=True)
        return default + fallbacks + others

    def get_model_config(self, provider: str, model_name: str) -> Optional[ModelConfig]:
        return self._cache.get(f"{provider}/{model_name}")

    def get_resolved_config(self, provider: str, model_name: str) -> dict:
        """获取完整配置（含API Key和base_url）"""
        mc = self.get_model_config(provider, model_name)
        preset = PROVIDER_PRESETS.get(provider)
        base_url = mc.base_url if mc and mc.base_url else (preset.base_url if preset else "")
        api_key = self.get_api_key(provider)
        return {
            "provider": provider,
            "model": model_name,
            "api_key": api_key,
            "base_url": base_url,
            "max_tokens": mc.max_tokens if mc else 4096,
            "temperature": mc.temperature if mc else 0.7,
            "timeout": mc.timeout_seconds if mc else 60,
        }

    def get_enabled_providers(self) -> list[str]:
        """获取已配置API Key的供应商"""
        result = []
        for provider in PROVIDER_PRESETS:
            if self._credential and self._credential.has_credential(provider):
                result.append(provider)
        return result

    def test_connection(self, provider: str, model_name: str = None) -> tuple[bool, str]:
        """测试模型连接"""
        api_key = self.get_api_key(provider)
        if not api_key and provider != "ollama":
            return False, "未配置API Key"
        # 简单的连接测试（实际调用需要model gateway）
        info = self.get_provider_info(provider)
        if not info:
            return False, f"未知供应商: {provider}"
        return True, f"供应商 {info.name} 已配置"


# 全局实例
_model_manager: Optional[ModelManager] = None


def get_model_manager(db=None, credential_manager=None) -> ModelManager:
    global _model_manager
    if _model_manager is None:
        _model_manager = ModelManager(db, credential_manager)
    return _model_manager
