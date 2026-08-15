"""
生图模型配置管理器

将生图服务商/模型/Key 持久化到 ~/.office_agent/image_model.json，
Key 使用与语言模型相同的 Fernet 加密（兼容旧 XOR 数据）。

生图服务商不绑死 Agnes：通过 provider + base_url + model 可配置任意
OpenAI 兼容的 /images/generations 端点，或通过 mcp_url 走 MCP 网关。
"""
import json
from pathlib import Path
from typing import Optional

from ..model_gateway.model_manager import ApiKeyCrypto


class ImageModelConfigManager:
    """生图模型配置管理器"""

    def __init__(self, config_dir: Optional[str] = None):
        if config_dir:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = Path.home() / ".office_agent"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "image_model.json"
        self._encryption = ApiKeyCrypto(self.config_dir)
        self._config = self._load()

    def _load(self) -> dict:
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {
                    "provider": data.get("provider", "agnes"),
                    "api_key": self._encryption.decrypt(data.get("api_key_enc", "")),
                    "base_url": data.get("base_url", ""),
                    "model": data.get("model", ""),
                    "mcp_url": data.get("mcp_url", ""),
                }
            except Exception:
                pass
        return {"provider": "agnes", "api_key": "", "base_url": "", "model": "", "mcp_url": ""}

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
        self._config = {
            "provider": provider,
            "api_key": effective_key,
            "base_url": (base_url or self._config["base_url"]).strip(),
            "model": (model or self._config["model"]).strip(),
            "mcp_url": (mcp_url or self._config["mcp_url"]).strip(),
        }
        data = {
            "provider": provider,
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
