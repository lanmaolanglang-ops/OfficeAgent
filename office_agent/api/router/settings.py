"""
设置管理路由 - 本地模型配置

职责：
- 保存/读取本地 API Key 配置（~/.office_agent/models.json）
- 复用 ModelGateway / ModelManager / SimpleEncryption 能力
- 不写 SQLite、不写任务记录

注意：
- 仅本地单机模式，无账号、无云端同步
- API Key 通过 SimpleEncryption 混淆存储，接口不回传明文
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...models.model_schemas import ModelProvider, DEFAULT_MODEL_CONFIGS

logger = logging.getLogger("office_agent.api.settings")
router = APIRouter(prefix="/api/settings", tags=["设置"])

# 合法供应商白名单（对应 ModelProvider 枚举）
_ALLOWED_PROVIDERS = {"openai", "deepseek", "doubao", "qwen", "claude", "gemini", "agnes"}


class ModelSettingsRequest(BaseModel):
    """模型配置请求"""
    provider: str
    model: str = ""
    api_key: str = ""
    base_url: Optional[str] = None


def _mask_key(api_key: str) -> str:
    """生成 Key 掩码，不回传明文"""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return api_key[:2] + "****"
    return api_key[:6] + "****" + api_key[-4:]


def _get_gateway():
    from ...model_gateway import ModelGateway
    return ModelGateway()


@router.get("/model")
def get_model_settings():
    """获取当前已配置模型信息（不回传 API Key）"""
    try:
        gateway = _get_gateway()
        available = gateway.manager.list_available_models()
        if not available:
            return {"configured": False}
        config = available[0]
        return {
            "configured": True,
            "provider": config.provider.value,
            "model": config.model or "",
            "api_key_mask": _mask_key(config.api_key),
        }
    except Exception as e:
        logger.exception("读取模型配置失败")
        raise HTTPException(status_code=500, detail=f"读取模型配置失败: {e}")


@router.post("/model")
def save_model_settings(req: ModelSettingsRequest):
    """保存模型配置到 ~/.office_agent/models.json"""
    provider = (req.provider or "").strip().lower()
    if provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"不支持的模型供应商: {provider or '(空)'}")
    api_key = (req.api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key 不能为空")

    try:
        provider_enum = ModelProvider(provider)
        default_cfg = DEFAULT_MODEL_CONFIGS.get(provider_enum)
        model = (req.model or "").strip() or (default_cfg.model if default_cfg else "")
        base_url = (req.base_url or "").strip() or (default_cfg.base_url if default_cfg else "")

        gateway = _get_gateway()
        # 写入 {provider}-default，使其进入 DEFAULT_ROUTING 候选链
        gateway.add_provider(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            model_id=f"{provider}-default",
        )
        logger.info("模型配置已保存: provider=%s model=%s", provider, model)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("保存模型配置失败")
        raise HTTPException(status_code=500, detail=f"保存模型配置失败: {e}")

    return {
        "configured": True,
        "provider": provider,
        "model": model,
        "api_key_mask": _mask_key(api_key),
    }