"""Custom LLM provider registry backed by the existing ``models.json`` store.

Each provider is represented by its model rows and provider metadata in
``ModelConfig.extra_params``.  This deliberately extends the existing model
configuration source of truth instead of introducing a second secrets file.
"""
from __future__ import annotations

import copy
import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Iterable

from ..models.model_schemas import (
    ModelConfig,
    ModelProvider,
    TASK_CAPABILITY_REQUIREMENTS,
)
from ..security.endpoint_policy import (
    EndpointPolicyError,
    ProviderHTTPError,
    join_api_endpoint,
    normalize_api_base_url,
    request_json,
)
from .model_manager import ModelManager


OPENAI_COMPATIBLE = "openai_compatible"
ANTHROPIC_COMPATIBLE = "anthropic_compatible"
SUPPORTED_PROTOCOLS = {OPENAI_COMPATIBLE, ANTHROPIC_COMPATIBLE}
MAX_PROVIDER_MODELS = 100


@dataclass(frozen=True)
class ProviderConnectionResult:
    success: bool
    code: str
    message: str
    models: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "code": self.code,
            "message": self.message,
            "models": list(self.models),
        }


def _clean_models(values: Iterable[str]) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        if len(value) > 200 or any(ord(char) < 32 for char in value):
            raise ValueError("模型 ID 包含非法字符或长度超过 200")
        seen.add(value)
        models.append(value)
        if len(models) > MAX_PROVIDER_MODELS:
            raise ValueError(f"单个 Provider 最多保存 {MAX_PROVIDER_MODELS} 个模型")
    return models


def _model_row_id(provider_id: str, model: str) -> str:
    digest = hashlib.sha256(model.encode("utf-8")).hexdigest()[:12]
    return f"{provider_id}-{digest}"[:64]


def _provider_meta(config: ModelConfig) -> dict:
    value = config.extra_params if isinstance(config.extra_params, dict) else {}
    return value if value.get("provider_id") else {}


def discover_models(*, protocol: str, base_url: str, api_key: str,
                    allow_local_endpoint: bool = False) -> list[str]:
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ValueError("不支持的 Provider 协议")
    headers = {"Authorization": f"Bearer {api_key}"}
    if protocol == ANTHROPIC_COMPATIBLE:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    payload = request_json(
        join_api_endpoint(base_url, "models"),
        headers=headers,
        timeout=30,
        allow_local=allow_local_endpoint,
    )
    raw_models = payload.get("data", payload.get("models", []))
    if not isinstance(raw_models, list):
        raise EndpointPolicyError("模型列表响应缺少 data/models 数组")
    values: list[str] = []
    for item in raw_models:
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("name")
            if isinstance(model_id, str):
                values.append(model_id)
    return _clean_models(values)


def test_provider_connection(*, protocol: str, base_url: str, api_key: str,
                             model: str = "", allow_local_endpoint: bool = False) -> ProviderConnectionResult:
    if not api_key.strip():
        return ProviderConnectionResult(False, "missing_key", "请填写 API Key")
    try:
        models = discover_models(
            protocol=protocol, base_url=base_url, api_key=api_key,
            allow_local_endpoint=allow_local_endpoint,
        )
    except ProviderHTTPError as exc:
        if exc.status in {401, 403}:
            return ProviderConnectionResult(False, "authentication_failed", "认证失败，请检查 API Key")
        if exc.status == 404:
            models = []
        else:
            return ProviderConnectionResult(False, "http_error", f"服务返回 HTTP {exc.status}")
    except EndpointPolicyError as exc:
        return ProviderConnectionResult(False, "endpoint_unreachable", str(exc))
    except Exception:
        return ProviderConnectionResult(False, "endpoint_unreachable", "地址不可访问或服务未响应")

    chosen = str(model or "").strip() or (models[0] if models else "")
    if not chosen:
        return ProviderConnectionResult(True, "no_models", "连接成功，但没有发现模型；可以手工添加模型 ID", tuple(models))

    try:
        if protocol == ANTHROPIC_COMPATIBLE:
            result = request_json(
                join_api_endpoint(base_url, "messages"), method="POST",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                payload={
                    "model": chosen,
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": "Reply OK"}],
                },
                timeout=45, allow_local=allow_local_endpoint,
            )
            valid = isinstance(result.get("content"), list)
        else:
            result = request_json(
                join_api_endpoint(base_url, "chat/completions"), method="POST",
                headers={"Authorization": f"Bearer {api_key}"},
                payload={
                    "model": chosen,
                    "messages": [{"role": "user", "content": "Reply OK"}],
                    "max_tokens": 16,
                    "stream": False,
                },
                timeout=45, allow_local=allow_local_endpoint,
            )
            valid = isinstance(result.get("choices"), list)
        if not valid:
            return ProviderConnectionResult(False, "protocol_incompatible", "接口协议不兼容")
    except ProviderHTTPError as exc:
        if exc.status in {401, 403}:
            return ProviderConnectionResult(False, "authentication_failed", "认证失败，请检查 API Key")
        if exc.status == 404:
            return ProviderConnectionResult(False, "protocol_incompatible", "接口协议不兼容或端点不存在")
        if exc.status == 429:
            return ProviderConnectionResult(False, "rate_limited", "认证成功，但服务限流或额度不足")
        return ProviderConnectionResult(False, "http_error", f"服务返回 HTTP {exc.status}")
    except EndpointPolicyError as exc:
        return ProviderConnectionResult(False, "endpoint_unreachable", str(exc))
    except Exception:
        return ProviderConnectionResult(False, "endpoint_unreachable", "地址不可访问或服务未响应")

    return ProviderConnectionResult(True, "connected", "连接成功", tuple(models))


class ProviderRegistry:
    """Aggregate custom provider records from existing model rows."""

    def __init__(self, manager: ModelManager | None = None):
        self.manager = manager or ModelManager()

    def list_custom(self, *, include_secret: bool = False) -> list[dict]:
        groups: dict[str, list[ModelConfig]] = {}
        for config in self.manager.list_models():
            meta = _provider_meta(config)
            if config.provider is ModelProvider.CUSTOM and meta:
                groups.setdefault(str(meta["provider_id"]), []).append(config)
        result: list[dict] = []
        for provider_id, configs in groups.items():
            first = configs[0]
            meta = _provider_meta(first)
            models = [config.model for config in configs]
            entry = {
                "id": provider_id,
                "name": str(meta.get("provider_name") or first.display_name),
                "protocol": str(meta.get("protocol") or OPENAI_COMPATIBLE),
                "base_url": first.base_url,
                "enabled": any(config.enabled for config in configs),
                "models": models,
                "default_model": str(meta.get("provider_default_model") or models[0]),
                "allow_local_endpoint": bool(meta.get("allow_local_endpoint", False)),
                "api_key_mask": "" if not first.api_key else ("****" if len(first.api_key) <= 4 else "****" + first.api_key[-4:]),
            }
            if include_secret:
                entry["api_key"] = first.api_key
            result.append(entry)
        return sorted(result, key=lambda item: item["name"].casefold())

    def get(self, provider_id: str, *, include_secret: bool = False) -> dict | None:
        return next((item for item in self.list_custom(include_secret=include_secret)
                     if item["id"] == provider_id), None)

    def save(self, *, name: str, protocol: str, base_url: str,
             api_key: str, models: Iterable[str], default_model: str,
             enabled: bool = True, allow_local_endpoint: bool = False,
             provider_id: str = "", clear_api_key: bool = False) -> dict:
        name = str(name or "").strip()
        if not name or len(name) > 128:
            raise ValueError("Provider Name 不能为空且不能超过 128 个字符")
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ValueError("不支持的 Provider 协议")
        normalized_base = normalize_api_base_url(base_url)
        clean_models = _clean_models(models)
        if not clean_models:
            raise ValueError("请自动检测或手工添加至少一个模型")
        default_model = str(default_model or "").strip() or clean_models[0]
        if default_model not in clean_models:
            raise ValueError("默认模型必须在模型列表中")

        existing = self.get(provider_id, include_secret=True) if provider_id else None
        if existing:
            effective_key = "" if clear_api_key else (api_key.strip() or str(existing.get("api_key") or ""))
        else:
            provider_id = f"provider_{uuid.uuid4().hex[:12]}"
            effective_key = api_key.strip()
        if not effective_key and enabled:
            raise ValueError("启用的 Provider 必须配置 API Key")
        if not re.fullmatch(r"provider_[a-f0-9]{12}", provider_id):
            raise ValueError("Provider ID 无效")

        manager = self.manager
        old_models = copy.deepcopy(manager._models)
        old_routing = copy.deepcopy(manager._routing)
        old_default = manager._default_model_id
        try:
            removed_ids = {
                model_id for model_id, config in manager._models.items()
                if _provider_meta(config).get("provider_id") == provider_id
            }
            for model_id in removed_ids:
                manager._models.pop(model_id, None)
            for model_ids in manager._routing.values():
                model_ids[:] = [model_id for model_id in model_ids if model_id not in removed_ids]

            created_ids: list[str] = []
            for index, model in enumerate(clean_models):
                model_id = _model_row_id(provider_id, model)
                created_ids.append(model_id)
                manager._models[model_id] = ModelConfig(
                    id=model_id,
                    provider=ModelProvider.CUSTOM,
                    display_name=f"{name} · {model}",
                    api_key=effective_key,
                    base_url=normalized_base,
                    model=model,
                    enabled=bool(enabled),
                    priority=40 + index,
                    extra_params={
                        "provider_id": provider_id,
                        "provider_name": name,
                        "protocol": protocol,
                        "provider_default_model": default_model,
                        "allow_local_endpoint": bool(allow_local_endpoint),
                    },
                )
            if not manager._routing:
                from ..models.model_schemas import DEFAULT_ROUTING
                manager._routing = {task.value: list(ids) for task, ids in DEFAULT_ROUTING.items()}
            default_id = _model_row_id(provider_id, default_model)
            for task_type, model_ids in manager._routing.items():
                requirement = TASK_CAPABILITY_REQUIREMENTS.get(str(task_type))
                if requirement and requirement[1]:
                    # Custom OpenAI/Anthropic-compatible providers are text-only
                    # until an explicit capability is configured.  Never place
                    # them in a hard-gated route such as image understanding.
                    continue
                for model_id in reversed(created_ids):
                    if model_id not in model_ids:
                        model_ids.insert(0, model_id)
            if old_default in removed_ids:
                manager._default_model_id = default_id
            elif not manager._default_model_id:
                manager._default_model_id = default_id
            manager._save_config()
        except Exception:
            manager._models = old_models
            manager._routing = old_routing
            manager._default_model_id = old_default
            raise
        saved = self.get(provider_id)
        if not saved:
            raise RuntimeError("Provider 保存后无法读取")
        return saved

    def delete(self, provider_id: str) -> bool:
        manager = self.manager
        old_models = copy.deepcopy(manager._models)
        old_routing = copy.deepcopy(manager._routing)
        old_default = manager._default_model_id
        removed = {
            model_id for model_id, config in manager._models.items()
            if _provider_meta(config).get("provider_id") == provider_id
        }
        if not removed:
            return False
        try:
            for model_id in removed:
                manager._models.pop(model_id, None)
            for model_ids in manager._routing.values():
                model_ids[:] = [model_id for model_id in model_ids if model_id not in removed]
            if manager._default_model_id in removed:
                manager._default_model_id = None
            manager._save_config()
            return True
        except Exception:
            manager._models = old_models
            manager._routing = old_routing
            manager._default_model_id = old_default
            raise
