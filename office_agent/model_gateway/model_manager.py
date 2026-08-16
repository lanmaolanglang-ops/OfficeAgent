"""
Model Manager - 模型配置管理
负责 API Key 的加密存储、模型配置的增删改查
"""
import json
import logging
import os
import base64
import hashlib
from pathlib import Path
from typing import Optional

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

from ..models.model_schemas import (
    ModelConfig, ModelProvider, DEFAULT_MODEL_CONFIGS, DEFAULT_ROUTING, AITaskType
)
from .clients import (
    BaseModelClient, OpenAIClient, DoubaoClient, ClaudeClient, GeminiClient
)

logger = logging.getLogger("office_agent.model_manager")


class SimpleEncryption:
    """旧版 XOR 加密器（仅用于回退解密历史配置，不再用于新写入）"""

    def __init__(self):
        # 使用机器名+用户名作为密钥种子（与旧版一致）
        machine = os.environ.get("COMPUTERNAME", "default")
        user = os.environ.get("USERNAME", "default")
        seed = f"office-agent-{machine}-{user}"
        self._key = hashlib.sha256(seed.encode()).digest()

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        text_bytes = plaintext.encode("utf-8")
        encrypted = bytearray()
        for i, b in enumerate(text_bytes):
            encrypted.append(b ^ self._key[i % len(self._key)])
        return base64.b64encode(bytes(encrypted)).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        try:
            encrypted = base64.b64decode(ciphertext.encode("utf-8"))
            decrypted = bytearray()
            for i, b in enumerate(encrypted):
                decrypted.append(b ^ self._key[i % len(self._key)])
            return bytes(decrypted).decode("utf-8")
        except Exception:
            return ""


class ApiKeyCrypto:
    """API Key 加密器：PBKDF2 + Fernet（密钥由机器信息派生，salt 持久化），兼容旧 XOR 数据。"""

    def __init__(self, config_dir: Path):
        self._legacy = SimpleEncryption()
        self._fernet = None
        if HAS_CRYPTO:
            self._salt_file = config_dir / "key_salt.bin"
            salt = self._load_or_create_salt()
            key = base64.urlsafe_b64encode(
                hashlib.pbkdf2_hmac("sha256", self._machine_key().encode("utf-8"), salt, 100000)
            )
            self._fernet = Fernet(key)

    def _machine_key(self) -> str:
        parts = [
            os.environ.get("COMPUTERNAME", os.environ.get("HOSTNAME", "unknown")),
            os.environ.get("USERNAME", os.environ.get("USER", "unknown")),
            str(Path.home()),
        ]
        return "|".join(parts)

    def _load_or_create_salt(self) -> bytes:
        if self._salt_file.exists():
            try:
                with open(self._salt_file, "rb") as f:
                    salt = f.read()
                if salt:
                    return salt
            except Exception:
                pass
        salt = os.urandom(32)
        try:
            with open(self._salt_file, "wb") as f:
                f.write(salt)
        except Exception:
            pass
        return salt

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        if self._fernet is not None:
            return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        return self._legacy.encrypt(plaintext)

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        if self._fernet is not None:
            try:
                return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
            except Exception:
                pass
        # 回退旧 XOR 数据
        return self._legacy.decrypt(ciphertext)


class ModelManager:
    """模型管理器"""
    
    def __init__(self, config_dir: Optional[str] = None):
        if config_dir:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = Path.home() / ".office_agent"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "models.json"
        
        self._encryption = ApiKeyCrypto(self.config_dir)
        self._models: dict[str, ModelConfig] = {}
        self._routing: dict[str, list[str]] = {}
        self._default_model_id: Optional[str] = None
        
        # 先加载环境变量中的 API Key
        self._load_env_keys()
        # 再加载配置文件（覆盖环境变量）
        self._load_config()
    
    def _load_env_keys(self):
        """从环境变量加载 API Key"""
        env_map = {
            "OPENAI_API_KEY": (ModelProvider.OPENAI, "openai-default", "OPENAI_BASE_URL", "OPENAI_MODEL"),
            "DEEPSEEK_API_KEY": (ModelProvider.DEEPSEEK, "deepseek-default", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"),
            "DOUBAO_API_KEY": (ModelProvider.DOUBAO, "doubao-default", "DOUBAO_BASE_URL", "DOUBAO_MODEL"),
            "DASHSCOPE_API_KEY": (ModelProvider.QWEN, "qwen-default", None, None),
            "ANTHROPIC_API_KEY": (ModelProvider.CLAUDE, "claude-default", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"),
            "GEMINI_API_KEY": (ModelProvider.GEMINI, "gemini-default", None, None),
            "AGNES_API_KEY": (ModelProvider.AGNES, "agnes-default", "AGNES_BASE_URL", "AGNES_MODEL"),
        }
        
        for env_key, (provider, model_id, base_url_env, model_env) in env_map.items():
            api_key = os.environ.get(env_key, "")
            if api_key:
                config = DEFAULT_MODEL_CONFIGS.get(provider)
                if config:
                    config.api_key = api_key
                    if base_url_env and os.environ.get(base_url_env):
                        config.base_url = os.environ[base_url_env]
                    if model_env and os.environ.get(model_env):
                        config.model = os.environ[model_env]
                    self._models[model_id] = config
    
    def _load_config(self):
        """从配置文件加载"""
        if not self.config_file.exists():
            self._save_config()
            return
        
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # 加载模型配置
            for model_data in data.get("models", []):
                model_id = model_data["id"]
                provider = ModelProvider(model_data["provider"])
                
                # 合并默认配置
                default = DEFAULT_MODEL_CONFIGS.get(provider)
                if default:
                    config = ModelConfig(
                        id=model_id,
                        provider=provider,
                        display_name=model_data.get("display_name", default.display_name),
                        api_key=self._encryption.decrypt(model_data.get("api_key_enc", "")),
                        base_url=model_data.get("base_url", default.base_url),
                        model=model_data.get("model", default.model),
                        enabled=model_data.get("enabled", True),
                        priority=model_data.get("priority", default.priority),
                        max_tokens=model_data.get("max_tokens", default.max_tokens),
                        temperature=model_data.get("temperature", default.temperature),
                        timeout=model_data.get("timeout", default.timeout),
                        supports_vision=model_data.get("supports_vision", default.supports_vision),
                        supports_document=model_data.get("supports_document", default.supports_document),
                    )
                else:
                    config = ModelConfig(
                        id=model_id,
                        provider=provider,
                        display_name=model_data.get("display_name", model_id),
                        api_key=self._encryption.decrypt(model_data.get("api_key_enc", "")),
                        base_url=model_data.get("base_url", ""),
                        model=model_data.get("model", ""),
                        enabled=model_data.get("enabled", True),
                    )
                
                # 优先级：models.json 中的 Key 优先于环境变量；
                # 文件无 Key 时回退使用环境变量 Key
                if model_id in self._models and self._models[model_id].api_key and not config.api_key:
                    config.api_key = self._models[model_id].api_key
                
                self._models[model_id] = config
            
            # 加载路由配置
            routing_data = data.get("routing", {})
            for task_type, model_ids in routing_data.items():
                self._routing[task_type] = model_ids

            # 加载默认模型
            self._default_model_id = data.get("default_model_id") or None
                
        except Exception as e:
            logger.warning("加载模型配置失败: %s", e)
    
    def _save_config(self):
        """保存配置到文件"""
        data = {
            "models": [],
            "routing": {},
            "default_model_id": self._default_model_id,
        }
        
        for model_id, config in self._models.items():
            model_data = {
                "id": config.id,
                "provider": config.provider.value,
                "display_name": config.display_name,
                "api_key_enc": self._encryption.encrypt(config.api_key),
                "base_url": config.base_url,
                "model": config.model,
                "enabled": config.enabled,
                "priority": config.priority,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
                "timeout": config.timeout,
                "supports_vision": config.supports_vision,
                "supports_document": config.supports_document,
            }
            data["models"].append(model_data)
        
        # 保存路由
        for task_type, model_ids in self._routing.items():
            data["routing"][task_type] = model_ids
        
        # 如果没有自定义路由，使用默认
        if not data["routing"]:
            for task_type, model_ids in DEFAULT_ROUTING.items():
                data["routing"][task_type.value] = model_ids
        
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("保存模型配置失败: %s", e)
    
    def add_model(self, config: ModelConfig) -> bool:
        """添加或更新模型配置"""
        self._models[config.id] = config
        # 首次保存的模型自动设为默认
        if not self._default_model_id or self._default_model_id not in self._models:
            self._default_model_id = config.id
        self._save_config()
        return True

    def set_default_model(self, model_id: str) -> bool:
        """设置当前默认模型（用于任务路由首选）"""
        if model_id not in self._models:
            return False
        self._default_model_id = model_id
        self._save_config()
        return True

    def get_default_model_id(self) -> Optional[str]:
        """获取当前默认模型ID（无则回退到第一个可用模型）"""
        if self._default_model_id and self._default_model_id in self._models:
            return self._default_model_id
        available = self.list_available_models()
        return available[0].id if available else None

    def get_default_model(self) -> Optional[ModelConfig]:
        """获取当前默认模型配置"""
        mid = self.get_default_model_id()
        return self._models.get(mid) if mid else None
    
    def remove_model(self, model_id: str) -> bool:
        """删除模型配置"""
        if model_id in self._models:
            del self._models[model_id]
            if self._default_model_id == model_id:
                self._default_model_id = None
            self._save_config()
            return True
        return False
    
    def get_model(self, model_id: str) -> Optional[ModelConfig]:
        """获取模型配置"""
        return self._models.get(model_id)
    
    def list_models(self, only_enabled: bool = False) -> list[ModelConfig]:
        """列出所有模型"""
        models = list(self._models.values())
        if only_enabled:
            models = [m for m in models if m.enabled and m.api_key]
        return sorted(models, key=lambda m: m.priority)
    
    def list_available_models(self) -> list[ModelConfig]:
        """列出可用模型（已配置 API Key 且启用）"""
        return [m for m in self._models.values() if m.enabled and m.api_key]
    
    def get_client(self, model_id: str) -> Optional[BaseModelClient]:
        """获取模型客户端实例"""
        config = self._models.get(model_id)
        if not config or not config.api_key:
            return None
        
        client_map = {
            ModelProvider.OPENAI: OpenAIClient,
            ModelProvider.DEEPSEEK: OpenAIClient,  # DeepSeek 兼容 OpenAI
            ModelProvider.QWEN: OpenAIClient,      # 通义兼容 OpenAI
            ModelProvider.CUSTOM: OpenAIClient,    # 自定义兼容 OpenAI
            ModelProvider.AGNES: OpenAIClient,     # Agnes 兼容 OpenAI
            ModelProvider.DOUBAO: DoubaoClient,
            ModelProvider.CLAUDE: ClaudeClient,
            ModelProvider.GEMINI: GeminiClient,
        }
        
        client_class = client_map.get(config.provider)
        if client_class:
            return client_class(config)
        return None
    
    def set_routing(self, task_type: AITaskType, model_ids: list[str]):
        """设置路由策略"""
        self._routing[task_type.value] = model_ids
        self._save_config()
    
    def get_routing(self, task_type) -> list[str]:
        """获取路由策略"""
        if isinstance(task_type, str):
            task_type = AITaskType(task_type)
        # 先查自定义路由
        if task_type.value in self._routing:
            return self._routing[task_type.value]
        # 再查默认路由
        if task_type in DEFAULT_ROUTING:
            return DEFAULT_ROUTING[task_type]
        # 返回所有可用模型
        return [m.id for m in self.list_available_models()]
    
    def test_model(self, model_id: str) -> tuple[bool, str]:
        """测试模型连接"""
        client = self.get_client(model_id)
        if not client:
            return False, "模型未配置或缺少 API Key"
        
        try:
            result = client.test_connection()
            if result.success:
                return True, f"连接成功，延迟 {result.latency_ms}ms"
            else:
                return False, result.error
        except Exception as e:
            return False, str(e)
    
    def get_config_path(self) -> str:
        """获取配置文件路径"""
        return str(self.config_file)
