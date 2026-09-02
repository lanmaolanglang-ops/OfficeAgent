"""
Model Manager - 模型配置管理
负责 API Key 的加密存储、模型配置的增删改查
"""
import json
import logging
import os
import base64
import hashlib
import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import replace
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
from ..runtime_config import get_data_root

logger = logging.getLogger("office_agent.model_manager")


@contextmanager
def _advisory_file_lock(path: Path):
    """跨进程配置锁（Windows msvcrt / POSIX flock）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def resolve_model_config_dir(config_dir: Optional[str] = None) -> Path:
    """解析模型配置目录，并从旧目录做一次不覆盖迁移。"""
    if config_dir:
        return Path(config_dir)

    legacy = Path.home() / ".office_agent"
    target = get_data_root()
    target.mkdir(parents=True, exist_ok=True)

    if target != legacy and legacy.exists():
        legacy_master = legacy / "master.key"
        target_master = target / "master.key"
        if legacy_master.is_file() and not target_master.exists():
            try:
                shutil.copy2(legacy_master, target_master)
            except OSError as exc:
                logger.warning("迁移模型主密钥失败 %s: %s", legacy_master, exc)

        legacy_salt = legacy / "key_salt.bin"
        target_salt = target / "key_salt.bin"
        if legacy_salt.is_file() and not target_salt.exists():
            try:
                shutil.copy2(legacy_salt, target_salt)
            except OSError as exc:
                logger.warning("迁移旧模型密钥盐失败 %s: %s", legacy_salt, exc)

        # Fernet 密文必须和生成它的 salt 一起迁移。目标目录已有不同 salt 时
        # 不复制旧密文，避免制造“文件存在但永远无法解密”的假配置。
        encryption_compatible = not HAS_CRYPTO
        if legacy_master.is_file() and target_master.is_file():
            try:
                encryption_compatible = legacy_master.read_bytes() == target_master.read_bytes()
            except OSError:
                encryption_compatible = False
        elif legacy_salt.is_file() and target_salt.is_file():
            try:
                encryption_compatible = legacy_salt.read_bytes() == target_salt.read_bytes()
            except OSError:
                encryption_compatible = False
        elif not legacy_master.exists() and not legacy_salt.exists():
            encryption_compatible = True
        if encryption_compatible:
            for name in ("models.json", "image_model.json"):
                source = legacy / name
                destination = target / name
                if source.is_file() and not destination.exists():
                    try:
                        shutil.copy2(source, destination)
                    except OSError as exc:
                        logger.warning("迁移旧模型配置失败 %s: %s", source, exc)
    return target


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
    """使用随机主密钥加密 API Key，并兼容一次性读取旧密文。"""

    _PREFIX = "v2:"

    def __init__(self, config_dir: Path):
        if not HAS_CRYPTO:
            raise RuntimeError("cryptography 不可用，拒绝以不安全方式保存 API Key")
        config_dir.mkdir(parents=True, exist_ok=True)
        self._legacy = SimpleEncryption()
        self._master_key_file = config_dir / "master.key"
        self._salt_file = config_dir / "key_salt.bin"
        self._fernet = Fernet(self._load_or_create_master_key())
        self._legacy_fernet = self._build_legacy_fernet()
        self.migration_required = False

    def _load_or_create_master_key(self) -> bytes:
        if self._master_key_file.exists():
            key = self._master_key_file.read_bytes().strip()
            try:
                Fernet(key)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("模型密钥主密钥文件已损坏") from exc
            return key

        key = Fernet.generate_key()
        fd, temp_name = tempfile.mkstemp(
            prefix=".master.key.", suffix=".tmp", dir=str(self._master_key_file.parent)
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temp_name, 0o600)
            except OSError:
                logger.warning("无法收紧模型主密钥文件权限: %s", temp_name)
            os.replace(temp_name, self._master_key_file)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return key

    def _build_legacy_fernet(self):
        if not self._salt_file.exists():
            return None
        try:
            salt = self._salt_file.read_bytes()
            if not salt:
                return None
            key = base64.urlsafe_b64encode(
                hashlib.pbkdf2_hmac(
                    "sha256", self._machine_key().encode("utf-8"), salt, 100000
                )
            )
            return Fernet(key)
        except OSError:
            return None

    def _machine_key(self) -> str:
        parts = [
            os.environ.get("COMPUTERNAME", os.environ.get("HOSTNAME", "unknown")),
            os.environ.get("USERNAME", os.environ.get("USER", "unknown")),
            str(Path.home()),
        ]
        return "|".join(parts)

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        return self._PREFIX + token

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        if ciphertext.startswith(self._PREFIX):
            try:
                token = ciphertext[len(self._PREFIX):]
                return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
            except Exception as exc:
                raise ValueError("无法解密模型 API Key，主密钥可能不匹配") from exc
        if self._legacy_fernet is not None:
            try:
                value = self._legacy_fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
                self.migration_required = True
                return value
            except Exception:
                pass
        value = self._legacy.decrypt(ciphertext)
        if value:
            self.migration_required = True
            return value
        raise ValueError("无法解密旧版模型 API Key")


class ModelManager:
    """模型管理器"""
    
    def __init__(self, config_dir: Optional[str] = None):
        self.config_dir = resolve_model_config_dir(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "models.json"
        self._config_lock_file = self.config_dir / ".models.json.lock"
        
        self._encryption = ApiKeyCrypto(self.config_dir)
        self._models: dict[str, ModelConfig] = {}
        self._routing: dict[str, list[str]] = {}
        self._default_model_id: Optional[str] = None
        self._config_lock = threading.RLock()
        
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
                template = DEFAULT_MODEL_CONFIGS.get(provider)
                config = replace(template) if template else None
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
            with self._config_lock, _advisory_file_lock(self._config_lock_file):
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            
            # 加载模型配置
            for index, model_data in enumerate(data.get("models", [])):
                try:
                    model_id = model_data.get("id")
                    if not model_id:
                        raise ValueError("缺少 id")
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
                except Exception as exc:
                    logger.warning("跳过损坏的模型配置 #%d: %s", index, exc)
            
            # 加载路由配置
            routing_data = data.get("routing", {})
            for task_type, model_ids in routing_data.items():
                self._routing[task_type] = model_ids

            # 加载默认模型
            self._default_model_id = data.get("default_model_id") or None

            if self._encryption.migration_required:
                self._save_config()
                logger.info("已将旧版模型 API Key 密文迁移为随机主密钥格式")
                
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
        
        temp_path = None
        try:
            with self._config_lock, _advisory_file_lock(self._config_lock_file):
                fd, temp_path = tempfile.mkstemp(
                    prefix=f".{self.config_file.name}.", suffix=".tmp",
                    dir=str(self.config_dir),
                )
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, self.config_file)
                temp_path = None
        except Exception as e:
            logger.error("保存模型配置失败: %s", e)
            raise RuntimeError("模型配置保存失败，内存变更未确认") from e
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
    
    def add_model(self, config: ModelConfig) -> bool:
        """添加或更新模型配置"""
        previous = self._models.get(config.id)
        previous_default = self._default_model_id
        self._models[config.id] = config
        # 首次保存的模型自动设为默认
        if not self._default_model_id or self._default_model_id not in self._models:
            self._default_model_id = config.id
        try:
            self._save_config()
        except Exception:
            if previous is None:
                self._models.pop(config.id, None)
            else:
                self._models[config.id] = previous
            self._default_model_id = previous_default
            raise
        return True

    def set_default_model(self, model_id: str) -> bool:
        """设置当前默认模型（用于任务路由首选）"""
        config = self._models.get(model_id)
        if not config or not config.enabled or not config.api_key:
            return False
        previous_default = self._default_model_id
        self._default_model_id = model_id
        try:
            self._save_config()
        except Exception:
            self._default_model_id = previous_default
            raise
        return True

    def get_default_model_id(self) -> Optional[str]:
        """获取当前默认模型ID（无则回退到第一个可用模型）"""
        default = self._models.get(self._default_model_id) if self._default_model_id else None
        if default and default.enabled and default.api_key:
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
            removed = self._models.pop(model_id)
            previous_default = self._default_model_id
            if self._default_model_id == model_id:
                self._default_model_id = None
            try:
                self._save_config()
            except Exception:
                self._models[model_id] = removed
                self._default_model_id = previous_default
                raise
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
        previous = self._routing.get(task_type.value)
        self._routing[task_type.value] = model_ids
        try:
            self._save_config()
        except Exception:
            if previous is None:
                self._routing.pop(task_type.value, None)
            else:
                self._routing[task_type.value] = previous
            raise
    
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
