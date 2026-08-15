"""
设置管理路由 - 本地模型配置

职责：
- 保存/读取本地 API Key 配置（~/.office_agent/models.json）
- 列出已保存的模型，支持切换默认模型（无需重新输入 API Key）
- API Key 通过 ApiKeyCrypto 加密存储，接口不回传明文
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...models.model_schemas import ModelProvider, DEFAULT_MODEL_CONFIGS
from ...image_generation.config import ImageModelConfigManager

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


class SetDefaultModelRequest(BaseModel):
    """切换默认模型请求"""
    model_id: str


class ImageModelRequest(BaseModel):
    """生图模型配置请求"""
    provider: str = "agnes"  # agnes | mcp
    api_key: str = ""
    base_url: Optional[str] = None
    model: str = ""
    mcp_url: Optional[str] = None


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
    """获取已配置的模型列表与当前默认模型（不回传 API Key）"""
    try:
        gateway = _get_gateway()
        manager = gateway.manager
        available = manager.list_available_models()
        if not available:
            return {"configured": False, "models": [], "default_model_id": None}
        default_id = manager.get_default_model_id()
        models = [
            {
                "id": m.id,
                "provider": m.provider.value,
                "model": m.model or "",
                "display_name": m.display_name,
                "api_key_mask": _mask_key(m.api_key),
                "is_default": m.id == default_id,
            }
            for m in available
        ]
        return {
            "configured": True,
            "default_model_id": default_id,
            "models": models,
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


@router.post("/model/default")
def set_default_model(req: SetDefaultModelRequest):
    """切换当前默认模型（已保存的 Key 无缝切换，无需重新输入）"""
    model_id = (req.model_id or "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id 不能为空")
    try:
        gateway = _get_gateway()
        if not gateway.manager.set_default_model(model_id):
            raise HTTPException(status_code=404, detail=f"模型不存在或未配置 API Key: {model_id}")
        config = gateway.manager.get_model(model_id)
        return {
            "configured": True,
            "default_model_id": model_id,
            "provider": config.provider.value,
            "model": config.model or "",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("切换默认模型失败")
        raise HTTPException(status_code=500, detail=f"切换默认模型失败: {e}")


@router.get("/image-model")
def get_image_model_settings():
    """获取生图模型配置（不回传 API Key）"""
    try:
        mgr = ImageModelConfigManager()
        cfg = mgr.get_config()
        return {
            "configured": mgr.is_configured(),
            "provider": cfg["provider"],
            "model": cfg["model"],
            "base_url": cfg["base_url"],
            "mcp_url": cfg["mcp_url"],
            "api_key_mask": _mask_key(cfg["api_key"]),
        }
    except Exception as e:
        logger.exception("读取生图模型配置失败")
        raise HTTPException(status_code=500, detail=f"读取生图模型配置失败: {e}")


@router.post("/image-model")
def save_image_model_settings(req: ImageModelRequest):
    """保存生图模型配置到 ~/.office_agent/image_model.json"""
    provider = (req.provider or "agnes").strip().lower()
    if provider not in ("agnes", "mcp"):
        raise HTTPException(status_code=400, detail=f"不支持的生图服务: {provider}（仅支持 agnes/mcp）")
    try:
        mgr = ImageModelConfigManager()
        cfg = mgr.save_config(
            provider=provider,
            api_key=(req.api_key or "").strip(),
            base_url=(req.base_url or "").strip(),
            model=(req.model or "").strip(),
            mcp_url=(req.mcp_url or "").strip(),
        )
        return {
            "configured": mgr.is_configured(),
            "provider": cfg["provider"],
            "model": cfg["model"],
            "base_url": cfg["base_url"],
            "mcp_url": cfg["mcp_url"],
            "api_key_mask": _mask_key(cfg["api_key"]),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("保存生图模型配置失败")
        raise HTTPException(status_code=500, detail=f"保存生图模型配置失败: {e}")
