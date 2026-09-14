"""Persistent configuration for the default RAG semantic embedding backend.

The frozen desktop build intentionally does not bundle torch/transformers, so
the local sentence-transformer backend is a development/optional path. The
normal desktop path is an OpenAI-compatible embedding provider configured by
the user through the settings API. This module stores that configuration in
the same encrypted model-config directory used by LLM and image models.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from ..model_gateway.model_manager import ApiKeyCrypto, resolve_model_config_dir

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_BASE_URL = "https://api.openai.com/v1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


def normalize_embedding_config(config: dict) -> dict:
    """Normalize an embedding provider configuration."""
    normalized = dict(config or {})
    normalized["provider"] = str(normalized.get("provider") or "custom").strip().lower()
    normalized["api_key"] = str(normalized.get("api_key") or "").strip()
    normalized["base_url"] = str(
        normalized.get("base_url") or DEFAULT_EMBEDDING_BASE_URL
    ).strip().rstrip("/")
    normalized["model"] = str(
        normalized.get("model") or DEFAULT_EMBEDDING_MODEL
    ).strip()
    return normalized


class EmbeddingConfigManager:
    """Manage the OpenAI-compatible semantic embedding configuration."""

    def __init__(self, config_dir: Optional[str] = None):
        self.config_dir = resolve_model_config_dir(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "embedding_config.json"
        self._encryption = ApiKeyCrypto(self.config_dir)
        self._config = self._load()

    def _load(self) -> dict:
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return normalize_embedding_config({
                    "provider": data.get("provider", "custom"),
                    "api_key": self._encryption.decrypt(data.get("api_key_enc", "")),
                    "base_url": data.get("base_url", ""),
                    "model": data.get("model", ""),
                })
            except Exception:
                # Fernet / legacy XOR decryption boundary. Keep a broad catch,
                # but never silently drop the fact that the config was unreadable.
                logger.warning(
                    "Embedding 配置加载失败，按未配置处理: %s",
                    self.config_file,
                    exc_info=True,
                )
        return normalize_embedding_config({"provider": "custom", "api_key": ""})

    def get_config(self) -> dict:
        return dict(self._config)

    def is_configured(self) -> bool:
        return bool(self._config["api_key"])

    def save_config(self, provider: str, api_key: str = "",
                    base_url: str = "", model: str = "") -> dict:
        """Save config; an empty api_key preserves any previously saved key."""
        effective_key = api_key.strip() if api_key else self._config["api_key"]
        self._config = normalize_embedding_config({
            "provider": provider,
            "api_key": effective_key,
            "base_url": (base_url or self._config["base_url"]).strip(),
            "model": (model or self._config["model"]).strip(),
        })
        data = {
            "provider": self._config["provider"],
            "api_key_enc": self._encryption.encrypt(effective_key),
            "base_url": self._config["base_url"],
            "model": self._config["model"],
        }
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return self.get_config()

    def clear_api_key(self) -> dict:
        """显式清空已保存的 API Key（P3-81）。

        ``save_config(api_key="")`` 的既有契约是“留空保留旧值”，因此需要
        一条独立、确定的清空路径，避免旧密钥永远无法被用户移除。
        """
        self._config = normalize_embedding_config({
            "provider": self._config["provider"],
            "api_key": "",
            "base_url": self._config["base_url"],
            "model": self._config["model"],
        })
        data = {
            "provider": self._config["provider"],
            "api_key_enc": self._encryption.encrypt(""),
            "base_url": self._config["base_url"],
            "model": self._config["model"],
        }
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return self.get_config()


def get_embedding_config() -> dict:
    """Return the current embedding provider config for internal callers."""
    return EmbeddingConfigManager().get_config()
