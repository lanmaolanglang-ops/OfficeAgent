"""
生图模型配置管理器

将生图服务商/模型/Key 持久化到统一数据目录的 image_model.json，
Key 使用与语言模型相同的 Fernet 加密（兼容旧 XOR 数据）。

生图服务商不绑死 Agnes：通过 provider + base_url + model 可配置任意
OpenAI 兼容的 /images/generations 端点，或通过 mcp_url 走 MCP 网关。
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

AGNES_DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"
AGNES_DEFAULT_MODEL = "agnes-image-2.0-flash"


def normalize_image_model_config(config: dict) -> dict:
    """规范化已知 Agnes 配置别名，同时保留自定义兼容端点。"""
    normalized = dict(config or {})
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
        self._config = self._load()

    def _load(self) -> dict:
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return normalize_image_model_config({
                    "provider": data.get("provider", "agnes"),
                    "api_key": self._encryption.decrypt(data.get("api_key_enc", "")),
                    "base_url": data.get("base_url", ""),
                    "model": data.get("model", ""),
                    "mcp_url": data.get("mcp_url", ""),
                })
            except Exception:
                # 解密边界异常类型不可穷举（Fernet/旧 XOR 双格式），保持 broad catch，
                # 但配置丢失必须留痕而非静默回退；日志不含 Key 内容。
                logger.warning(
                    f"生图配置加载失败，回退默认配置: {self.config_file}",
                    exc_info=True,
                )
        return normalize_image_model_config({"provider": "agnes", "api_key": ""})

    def get_config(self) -> dict:
        """返回生图配置（api_key 为明文，供内部调用）"""
        return dict(self._config)

    def is_configured(self) -> bool:
        c = self._config
        return bool(c["mcp_url"]) if c["provider"] == "mcp" else bool(c["api_key"])

    def save_config(self, provider: str, api_key: str = "", base_url: str = "",
                    model: str = "", mcp_url: str = "") -> dict:
        """保存配置；api_key 留空表示保留已保存的 Key"""
        provider = (provider or self._config["provider"] or "agnes").strip()
        effective_key = api_key.strip() if api_key else self._config["api_key"]
        self._config = normalize_image_model_config({
            "provider": provider,
            "api_key": effective_key,
            "base_url": (base_url or self._config["base_url"]).strip(),
            "model": (model or self._config["model"]).strip(),
            "mcp_url": (mcp_url or self._config["mcp_url"]).strip(),
        })
        data = {
            "provider": self._config["provider"],
            "api_key_enc": self._encryption.encrypt(effective_key),
            "base_url": self._config["base_url"],
            "model": self._config["model"],
            "mcp_url": self._config["mcp_url"],
        }
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return self.get_config()


def get_image_model_config() -> dict:
    """获取当前生图模型配置（供任务层读取）"""
    return ImageModelConfigManager().get_config()
