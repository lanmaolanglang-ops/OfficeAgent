"""
生图模型配置管理器

将生图服务商/模型/Key 持久化到统一数据目录的 image_model.json，
Key 使用与语言模型相同的 Fernet 加密（兼容旧 XOR 数据）。

生图服务商不绑死 Agnes：通过 provider + base_url + model 可配置任意
OpenAI 兼容的 /images/generations 端点，或通过 mcp_url 走 MCP 网关。
"""
import json
import logging
import re
import uuid
from typing import Optional

from ..persistence import atomic_write_json

logger = logging.getLogger(__name__)

AGNES_DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"
AGNES_DEFAULT_MODEL = "agnes-image-2.0-flash"
OPENAI_IMAGE_COMPATIBLE = "openai_image_compatible"
SUPPORTED_IMAGE_PROTOCOLS = {OPENAI_IMAGE_COMPATIBLE, "mcp"}


def normalize_image_model_config(config: dict) -> dict:
    """规范化已知 Agnes 配置别名，同时保留自定义兼容端点。"""
    normalized = dict(config or {})
    # The encrypted-at-rest representation is persistence metadata, not
    # runtime/provider state.  Dropping it here prevents it from leaking back
    # through public serializers after a configuration reload.
    normalized.pop("api_key_enc", None)
    provider = str(normalized.get("provider") or "agnes").strip().lower()
    normalized["provider"] = provider
    normalized["api_key"] = str(normalized.get("api_key") or "").strip()
    normalized["mcp_url"] = str(normalized.get("mcp_url") or "").strip().rstrip("/")

    base_url = str(normalized.get("base_url") or "").strip().rstrip("/")
    model = str(normalized.get("model") or "").strip()
    if provider == "agnes":
        # 修复历史设置页曾允许保存的常见拼写错误。
        if base_url.lower() == "https://apihub-agnes-ai.com/v1":
            base_url = AGNES_DEFAULT_BASE_URL
        if not base_url:
            base_url = AGNES_DEFAULT_BASE_URL
        if model.lower().startswith("agnes-image-"):
            model = "-".join(model.split())
        if not model:
            model = AGNES_DEFAULT_MODEL
    normalized["base_url"] = base_url
    normalized["model"] = model
    return normalized


class ImageModelConfigManager:
    """生图模型配置管理器"""

    def __init__(self, config_dir: Optional[str] = None):
        # Keep the lightweight image download gateway importable on its own.
        # Importing model_gateway at module load enters its package __init__,
        # which imports the API settings router and cycles back to this module.
        from ..model_gateway.model_manager import (
            ApiKeyCrypto,
            resolve_model_config_dir,
        )

        self.config_dir = resolve_model_config_dir(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "image_model.json"
        self._encryption = ApiKeyCrypto(self.config_dir)
        self._providers: dict[str, dict] = {}
        self._default_provider_id = "agnes"
        self._config = self._load()

    def _load(self) -> dict:
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                providers = data.get("providers")
                if isinstance(providers, list):
                    for item in providers:
                        if not isinstance(item, dict) or not item.get("id"):
                            continue
                        provider = normalize_image_model_config({
                            **item,
                            "api_key": self._encryption.decrypt(item.get("api_key_enc", "")),
                        })
                        provider["id"] = str(item["id"])
                        provider["name"] = str(item.get("name") or provider["id"])
                        provider["protocol"] = str(item.get("protocol") or (
                            "mcp" if provider["provider"] == "mcp" else OPENAI_IMAGE_COMPATIBLE
                        ))
                        provider["models"] = list(item.get("models") or ([provider["model"]] if provider["model"] else []))
                        provider["default_model"] = str(item.get("default_model") or provider["model"])
                        provider["enabled"] = bool(item.get("enabled", True))
                        provider["allow_local_endpoint"] = bool(item.get("allow_local_endpoint", False))
                        self._providers[provider["id"]] = provider
                    self._default_provider_id = str(data.get("default_provider_id") or "agnes")
                    selected = self._providers.get(self._default_provider_id)
                    if selected:
                        return dict(selected)
                legacy = normalize_image_model_config({
                    "provider": data.get("provider", "agnes"),
                    "api_key": self._encryption.decrypt(data.get("api_key_enc", "")),
                    "base_url": data.get("base_url", ""),
                    "model": data.get("model", ""),
                    "mcp_url": data.get("mcp_url", ""),
                })
                legacy.update({
                    "id": str(data.get("provider") or "agnes"),
                    "name": "Agnes AI" if data.get("provider", "agnes") == "agnes" else str(data.get("provider") or "Image Provider"),
                    "protocol": "mcp" if data.get("provider") == "mcp" else OPENAI_IMAGE_COMPATIBLE,
                    "models": [legacy["model"]] if legacy["model"] else [],
                    "default_model": legacy["model"],
                    "enabled": True,
                    "allow_local_endpoint": False,
                })
                self._providers[legacy["id"]] = legacy
                self._default_provider_id = legacy["id"]
                return legacy
            except Exception:
                # 解密边界异常类型不可穷举（Fernet/旧 XOR 双格式），保持 broad catch，
                # 但配置丢失必须留痕而非静默回退；日志不含 Key 内容。
                logger.warning(
                    f"生图配置加载失败，回退默认配置: {self.config_file}",
                    exc_info=True,
                )
        default = normalize_image_model_config({"provider": "agnes", "api_key": ""})
        default.update({
            "id": "agnes", "name": "Agnes AI", "protocol": OPENAI_IMAGE_COMPATIBLE,
            "models": [default["model"]], "default_model": default["model"],
            "enabled": True, "allow_local_endpoint": False,
        })
        self._providers["agnes"] = default
        return default

    def get_config(self) -> dict:
        """返回生图配置（api_key 为明文，供内部调用）"""
        return dict(self._config)

    def is_configured(self) -> bool:
        c = self._config
        if not c.get("enabled", True):
            return False
        return bool(c["mcp_url"]) if c["provider"] == "mcp" else bool(c["api_key"])

    def list_providers(self, *, include_secret: bool = False) -> list[dict]:
        result = []
        for provider in self._providers.values():
            item = dict(provider)
            key = str(item.pop("api_key", ""))
            item.pop("api_key_enc", None)
            item["api_key_mask"] = "" if not key else ("****" if len(key) <= 4 else "****" + key[-4:])
            item["is_default"] = item["id"] == self._default_provider_id
            if include_secret:
                item["api_key"] = key
            result.append(item)
        return sorted(result, key=lambda item: (not item["is_default"], item["name"].casefold()))

    def _persist(self) -> None:
        providers = []
        for provider in self._providers.values():
            item = {key: value for key, value in provider.items() if key != "api_key"}
            item["api_key_enc"] = self._encryption.encrypt(str(provider.get("api_key") or ""))
            providers.append(item)
        current = self._providers[self._default_provider_id]
        data = {
            "version": 2,
            "default_provider_id": self._default_provider_id,
            "providers": providers,
            # Legacy mirror keeps older application builds able to read the
            # selected provider during a rollback.
            "provider": current["provider"],
            "api_key_enc": self._encryption.encrypt(str(current.get("api_key") or "")),
            "base_url": current["base_url"],
            "model": current["model"],
            "mcp_url": current["mcp_url"],
        }
        atomic_write_json(self.config_file, data, indent=2, ensure_ascii=False)

    def save_provider(self, *, name: str, protocol: str, api_key: str = "",
                      base_url: str = "", models: list[str] | None = None,
                      default_model: str = "", enabled: bool = True,
                      allow_local_endpoint: bool = False, provider_id: str = "",
                      clear_api_key: bool = False, mcp_url: str = "") -> dict:
        name = str(name or "").strip()
        if not name or len(name) > 128:
            raise ValueError("Image Provider Name 不能为空且不能超过 128 个字符")
        if protocol not in SUPPORTED_IMAGE_PROTOCOLS:
            raise ValueError("不支持的生图协议")
        existing = self._providers.get(provider_id) if provider_id else None
        if not provider_id:
            provider_id = f"image_{uuid.uuid4().hex[:12]}"
        if provider_id not in {"agnes", "mcp"} and not re.fullmatch(r"image_[a-f0-9]{12}", provider_id):
            raise ValueError("Image Provider ID 无效")
        provider_kind = "mcp" if protocol == "mcp" else ("agnes" if provider_id == "agnes" else "custom")
        effective_key = "" if clear_api_key else (api_key.strip() or str((existing or {}).get("api_key") or ""))
        clean_models = []
        for raw in models or []:
            model = str(raw or "").strip()
            if model and model not in clean_models:
                clean_models.append(model)
        if len(clean_models) > 100 or any(len(model) > 200 for model in clean_models):
            raise ValueError("生图模型列表无效或超过 100 个")
        selected_model = str(default_model or "").strip() or (clean_models[0] if clean_models else "")
        if protocol != "mcp" and not selected_model:
            raise ValueError("请自动检测或手工添加至少一个生图模型")
        if selected_model and selected_model not in clean_models:
            clean_models.append(selected_model)
        normalized = normalize_image_model_config({
            "provider": provider_kind,
            "api_key": effective_key,
            "base_url": base_url or str((existing or {}).get("base_url") or ""),
            "model": selected_model,
            "mcp_url": mcp_url or str((existing or {}).get("mcp_url") or ""),
        })
        if enabled and protocol != "mcp" and not effective_key:
            raise ValueError("启用的 Image Provider 必须配置 API Key")
        if enabled and protocol == "mcp" and not normalized["mcp_url"]:
            raise ValueError("MCP Provider 必须配置 MCP URL")
        normalized.update({
            "id": provider_id, "name": name, "protocol": protocol,
            "models": clean_models, "default_model": selected_model,
            "enabled": bool(enabled), "allow_local_endpoint": bool(allow_local_endpoint),
        })
        previous = dict(self._providers)
        previous_default = self._default_provider_id
        try:
            self._providers[provider_id] = normalized
            if not self._default_provider_id or self._default_provider_id not in self._providers:
                self._default_provider_id = provider_id
            self._config = dict(self._providers[self._default_provider_id])
            self._persist()
        except Exception:
            self._providers = previous
            self._default_provider_id = previous_default
            self._config = dict(self._providers[self._default_provider_id])
            raise
        return dict(normalized)

    def set_default_provider(self, provider_id: str) -> dict:
        provider = self._providers.get(provider_id)
        if not provider or not provider.get("enabled", True):
            raise ValueError("Image Provider 不存在或未启用")
        previous = self._default_provider_id
        self._default_provider_id = provider_id
        self._config = dict(provider)
        try:
            self._persist()
        except Exception:
            self._default_provider_id = previous
            self._config = dict(self._providers[previous])
            raise
        return self.get_config()

    def delete_provider(self, provider_id: str) -> bool:
        if provider_id == "agnes" or provider_id not in self._providers:
            return False
        previous = dict(self._providers)
        previous_default = self._default_provider_id
        try:
            del self._providers[provider_id]
            if self._default_provider_id == provider_id:
                self._default_provider_id = "agnes" if "agnes" in self._providers else next(iter(self._providers))
            self._config = dict(self._providers[self._default_provider_id])
            self._persist()
            return True
        except Exception:
            self._providers = previous
            self._default_provider_id = previous_default
            self._config = dict(self._providers[previous_default])
            raise

    def save_config(self, provider: str, api_key: str = "", base_url: str = "",
                    model: str = "", mcp_url: str = "") -> dict:
        """保存配置；api_key 留空表示保留已保存的 Key"""
        provider = (provider or self._config["provider"] or "agnes").strip().lower()
        provider_id = provider if provider in {"agnes", "mcp"} else (
            self._config.get("id") if self._config.get("provider") == provider else ""
        )
        protocol = "mcp" if provider == "mcp" else OPENAI_IMAGE_COMPATIBLE
        saved = self.save_provider(
            provider_id=str(provider_id or ""),
            name="Agnes AI" if provider == "agnes" else ("MCP Image" if provider == "mcp" else "Custom Image Provider"),
            protocol=protocol,
            api_key=api_key,
            base_url=base_url,
            models=[model] if model else list(self._config.get("models") or []),
            default_model=model,
            enabled=True,
            mcp_url=mcp_url,
        )
        self.set_default_provider(saved["id"])
        return self.get_config()


def get_image_model_config() -> dict:
    """获取当前生图模型配置（供任务层读取）"""
    config = ImageModelConfigManager().get_config()
    if not config.get("enabled", True):
        # Keep the user's saved secret at rest while making the runtime gateway
        # explicitly unavailable.  PPT then follows its existing non-image
        # fallback path and records image_generation metadata.
        config["api_key"] = ""
        config["mcp_url"] = ""
    return config
