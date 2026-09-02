"""
设置管理路由 - 本地模型配置

职责：
- 保存/读取统一数据目录中的本地 API Key 配置
- 列出已保存的模型，支持切换默认模型（无需重新输入 API Key）
- API Key 通过 ApiKeyCrypto 加密存储，接口不回传明文
"""
import logging
import tempfile
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...models.model_schemas import ModelProvider, DEFAULT_MODEL_CONFIGS
from ...image_generation.config import ImageModelConfigManager
from ...image_generation.gateway import ImageGenerationGateway

logger = logging.getLogger("office_agent.api.settings")
router = APIRouter(prefix="/api/settings", tags=["设置"])

# 合法供应商白名单（对应 ModelProvider 枚举）
_ALLOWED_PROVIDERS = {
    provider.value for provider in ModelProvider if provider is not ModelProvider.CUSTOM
}


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


def _model_test_error(error: str) -> str:
    text = (error or "").lower()
    if "401" in text or "403" in text or "authentication" in text or "api key" in text:
        return "鉴权失败，请更新 API Key"
    if "429" in text or "rate limit" in text or "限流" in text:
        return "请求过于频繁或额度不足，请稍后重试"
    if "timeout" in text or "timed out" in text or "连接超时" in text:
        return "连接超时，请检查网络或服务地址"
    return "模型连接失败，请检查模型名称、服务地址和网络"


def _image_test_error(error: str) -> str:
    text = (error or "").lower()
    if "401" in text or "403" in text or "authentication" in text or "api key" in text:
        return "生图服务鉴权失败，请更新 API Key"
    if "429" in text or "rate limit" in text or "quota" in text or "额度" in text:
        return "生图服务额度不足或请求受限，请稍后重试"
    if "http 400" in text:
        return "生图请求被拒绝，请检查模型名称和参数"
    if "http 404" in text:
        return "未找到生图端点或模型，请检查 Base URL 和模型名称"
    if "getaddrinfo" in text or "name resolution" in text or "连接失败" in error:
        return "无法连接生图服务，请检查 Base URL 和网络"
    if "timeout" in text or "timed out" in text or "超时" in error:
        return "生图请求超时，请检查网络后重试"
    return "生图测试失败，请检查模型名称、服务地址和网络"


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
    except Exception:
        logger.exception("读取模型配置失败")
        raise HTTPException(status_code=500, detail="读取模型配置失败，请稍后重试")


@router.post("/model")
def save_model_settings(req: ModelSettingsRequest):
    """保存模型配置到统一数据目录的 models.json。"""
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
    except Exception:
        logger.exception("保存模型配置失败")
        raise HTTPException(status_code=500, detail="保存模型配置失败，请检查配置后重试")

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
    except Exception:
        logger.exception("切换默认模型失败")
        raise HTTPException(status_code=500, detail="切换默认模型失败，请稍后重试")


@router.post("/model/{model_id}/test")
async def test_model_connection(model_id: str):
    """按需发起一条短请求，验证已保存模型的真实可用性。"""
    gateway = _get_gateway()
    client = gateway.manager.get_client(model_id)
    if not client:
        raise HTTPException(status_code=404, detail="模型不存在、未启用或缺少 API Key")
    timeout = max(1, min(int(getattr(client.config, "timeout", 60)) + 5, 180))
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(client.simple_chat, "只回复 OK", max_tokens=128),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {
            "success": False, "model_id": model_id, "latency_ms": timeout * 1000,
            "message": "连接超时，请检查网络或服务地址",
        }
    return {
        "success": result.success,
        "model_id": model_id,
        "latency_ms": result.latency_ms,
        "message": "连接成功" if result.success else _model_test_error(result.error),
    }


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
    except Exception:
        logger.exception("读取生图模型配置失败")
        raise HTTPException(status_code=500, detail="读取生图模型配置失败，请稍后重试")


@router.post("/image-model")
def save_image_model_settings(req: ImageModelRequest):
    """保存生图模型配置到统一数据目录的 image_model.json。"""
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
    except Exception:
        logger.exception("保存生图模型配置失败")
        raise HTTPException(status_code=500, detail="保存生图模型配置失败，请检查配置后重试")


@router.post("/image-model/test")
async def test_image_model_connection():
    """生成并立即删除一张测试图，验证保存的生图配置是否真实可用。"""
    mgr = ImageModelConfigManager()
    cfg = mgr.get_config()
    if not mgr.is_configured():
        raise HTTPException(status_code=400, detail="请先保存生图配置")
    gateway = ImageGenerationGateway(
        api_key=cfg.get("api_key", ""),
        base_url=cfg.get("base_url", ""),
        model=cfg.get("model", ""),
        provider=cfg.get("provider", ""),
        mcp_url=cfg.get("mcp_url", ""),
    )
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(gateway.test_connection, output_dir=tempfile.gettempdir()),
            timeout=180,
        )
        return {
            **result,
            "provider": gateway.provider,
            "model": gateway.model,
            "message": "测试图生成成功，配置可用",
        }
    except (Exception, asyncio.TimeoutError) as exc:
        logger.warning("生图配置测试失败: %s", _image_test_error(str(exc)))
        return {
            "success": False,
            "provider": gateway.provider,
            "model": gateway.model,
            "latency_ms": 0,
            "message": _image_test_error(str(exc)),
        }
