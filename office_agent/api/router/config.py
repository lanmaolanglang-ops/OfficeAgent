"""配置管理 API 端点

读写同源：全部经由 ConfigManager 单例（模型配置注入权威存储
ModelManager 后与网关同源）。未知 key 由 pydantic extra="forbid" 拒绝，
不静默丢弃；错误沿用 FastAPI 422 / 既有 HTTPException 协议。
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, List, Dict, Any

from office_agent.config_system import get_config
from office_agent.logging_system import get_logger
from office_agent.models.model_schemas import ModelProvider, normalize_provider

logger = get_logger("api.config")
router = APIRouter(prefix="/api/config", tags=["配置管理"])


# ============================================================
# 请求模型
# ============================================================

def _validate_http_url(value: Optional[str]) -> Optional[str]:
    """端点必须为空串（回退 provider 默认）或 http(s) URL。"""
    if value and not value.startswith(("http://", "https://")):
        raise ValueError("api_endpoint 必须以 http:// 或 https:// 开头")
    return value


class ModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: Optional[str] = Field(None, min_length=1)
    provider: Optional[str] = Field(None, min_length=1)
    api_endpoint: Optional[str] = None
    api_key_env: Optional[str] = Field(None, min_length=1)
    temperature: Optional[float] = Field(None, ge=0, le=2)
    top_p: Optional[float] = Field(None, ge=0, le=1)
    max_tokens: Optional[int] = Field(None, ge=1)
    context_length: Optional[int] = Field(None, ge=1)
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    tags: Optional[List[str]] = None
    description: Optional[str] = None

    @field_validator("api_endpoint")
    @classmethod
    def _check_endpoint(cls, v):
        return _validate_http_url(v)


class AgentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = None
    system_prompt: Optional[str] = None
    model_priority: Optional[List[str]] = None
    available_tools: Optional[List[Dict[str, Any]]] = None
    timeout: Optional[int] = Field(None, ge=1)
    max_retries: Optional[int] = Field(None, ge=0)
    enabled: Optional[bool] = None
    enable_quality_check: Optional[bool] = None
    quality_threshold: Optional[float] = Field(None, ge=0, le=1)


class PromptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    content: str = Field(min_length=1)
    version: Optional[str] = None
    description: Optional[str] = None
    agent: Optional[str] = None
    variables: Optional[List[str]] = None
    set_default: bool = False


class PromptVersionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    set_default: Optional[bool] = None
    status: Optional[str] = None


class SkillUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = None
    workflow: Optional[List[Dict[str, Any]]] = None
    tools: Optional[List[str]] = None
    prompt: Optional[str] = None
    enabled: Optional[bool] = None
    trigger_keywords: Optional[List[str]] = None


class WorkflowUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None
    enabled: Optional[bool] = None
    timeout: Optional[int] = Field(None, ge=1)


# ============================================================
# 全局配置
# ============================================================

@router.get("/")
def get_global_config():
    """获取全局配置"""
    config = get_config()
    return {"success": True, "data": config.global_config.model_dump()}


@router.get("/export")
def export_all_config():
    """导出所有配置"""
    config = get_config()
    return {"success": True, "data": config.export_all()}


@router.post("/reload")
def reload_config():
    """重新加载配置（热更新）"""
    config = get_config()
    config.reload()
    logger.info("配置已重新加载")
    return {"success": True, "message": "配置已重新加载"}


# ============================================================
# 模型配置
# ============================================================

@router.get("/models")
def list_models(enabled_only: bool = Query(False)):
    """列出所有模型配置"""
    config = get_config()
    if enabled_only:
        models = config.get_enabled_models()
    else:
        models = list(config._models.values())
    return {"success": True, "data": models, "total": len(models)}


@router.get("/models/{model_id}")
def get_model(model_id: str):
    """获取模型配置"""
    config = get_config()
    model = config.get_model(model_id)
    if not model:
        raise HTTPException(404, f"模型不存在: {model_id}")
    return {"success": True, "data": model}


@router.put("/models/{model_id}")
def update_model(model_id: str, update: ModelUpdate):
    """更新模型配置"""
    config = get_config()
    if update.provider is not None:
        normalized = normalize_provider(update.provider)
        try:
            ModelProvider(normalized)
        except ValueError:
            raise HTTPException(400, f"未知模型供应商: {update.provider}")
    data = update.model_dump(exclude_none=True)
    model = config.update_model(model_id, data)
    logger.info(f"模型配置已更新: {model_id}")
    return {"success": True, "data": model}


# ============================================================
# Agent 配置
# ============================================================

@router.get("/agents")
def list_agents(enabled_only: bool = Query(False)):
    """列出所有 Agent 配置"""
    config = get_config()
    if enabled_only:
        agents = config.get_enabled_agents()
    else:
        agents = list(config._agents.values())
    return {"success": True, "data": agents, "total": len(agents)}


@router.get("/agents/{agent_name}")
def get_agent(agent_name: str):
    """获取 Agent 配置"""
    config = get_config()
    agent = config.get_agent(agent_name)
    if not agent:
        raise HTTPException(404, f"Agent 不存在: {agent_name}")
    return {"success": True, "data": agent}


@router.put("/agents/{agent_name}")
def update_agent(agent_name: str, update: AgentUpdate):
    """更新 Agent 配置"""
    config = get_config()
    data = update.model_dump(exclude_none=True)
    agent = config.update_agent(agent_name, data)
    logger.info(f"Agent 配置已更新: {agent_name}")
    return {"success": True, "data": agent}


@router.get("/agents/{agent_name}/prompt")
def get_agent_prompt(agent_name: str):
    """获取 Agent 的 Prompt"""
    config = get_config()
    prompt = config.get_agent_prompt(agent_name)
    return {"success": True, "data": {"agent": agent_name, "prompt": prompt}}


# ============================================================
# Prompt 管理
# ============================================================

@router.get("/prompts")
def list_prompts(agent: Optional[str] = None):
    """列出所有 Prompt"""
    config = get_config()
    result = []
    for name, versions in config._prompts.items():
        if agent and not any(v.get("agent") == agent for v in versions):
            continue
        result.append({
            "name": name,
            "versions": [
                {"version": v.get("version"), "status": v.get("status"),
                 "is_default": v.get("is_default"), "id": v.get("id")}
                for v in versions
            ],
        })
    return {"success": True, "data": result, "total": len(result)}


@router.get("/prompts/{name}")
def get_prompt(name: str, version: Optional[str] = None):
    """获取 Prompt"""
    config = get_config()
    prompt = config.get_prompt(name, version)
    if not prompt:
        raise HTTPException(404, f"Prompt 不存在: {name}")
    return {"success": True, "data": prompt}


@router.post("/prompts")
def create_prompt(prompt: PromptCreate):
    """创建/更新 Prompt 版本"""
    config = get_config()
    result = config.update_prompt(
        name=prompt.name,
        content=prompt.content,
        version=prompt.version,
        set_default=prompt.set_default,
    )
    logger.info(f"Prompt 已保存: {prompt.name} v{result['version']}")
    return {"success": True, "data": result}


@router.get("/prompts/{name}/versions")
def list_prompt_versions(name: str):
    """列出 Prompt 所有版本"""
    config = get_config()
    versions = config.list_prompt_versions(name)
    if not versions:
        raise HTTPException(404, f"Prompt 不存在: {name}")
    return {"success": True, "data": versions}


# ============================================================
# Skill 配置
# ============================================================

@router.get("/skills")
def list_skills(enabled_only: bool = Query(False)):
    """列出所有 Skill"""
    config = get_config()
    if enabled_only:
        skills = config.get_enabled_skills()
    else:
        skills = list(config._skills.values())
    return {"success": True, "data": skills, "total": len(skills)}


@router.get("/skills/{skill_name}")
def get_skill(skill_name: str):
    """获取 Skill 配置"""
    config = get_config()
    skill = config.get_skill(skill_name)
    if not skill:
        raise HTTPException(404, f"Skill 不存在: {skill_name}")
    return {"success": True, "data": skill}


@router.put("/skills/{skill_name}")
def update_skill(skill_name: str, update: SkillUpdate):
    """更新 Skill 配置"""
    config = get_config()
    data = update.model_dump(exclude_none=True)
    skill = config.update_skill(skill_name, data)
    logger.info(f"Skill 配置已更新: {skill_name}")
    return {"success": True, "data": skill}


# ============================================================
# Workflow 配置
# ============================================================

@router.get("/workflows")
def list_workflows(enabled_only: bool = Query(False)):
    """列出所有工作流"""
    config = get_config()
    if enabled_only:
        wfs = config.get_enabled_workflows()
    else:
        wfs = list(config._workflows.values())
    return {"success": True, "data": wfs, "total": len(wfs)}


@router.get("/workflows/{name}")
def get_workflow(name: str):
    """获取工作流配置"""
    config = get_config()
    wf = config.get_workflow(name)
    if not wf:
        raise HTTPException(404, f"工作流不存在: {name}")
    return {"success": True, "data": wf}


@router.put("/workflows/{name}")
def update_workflow(name: str, update: WorkflowUpdate):
    """更新工作流配置"""
    config = get_config()
    data = update.model_dump(exclude_none=True)
    wf = config.update_workflow(name, data)
    logger.info(f"工作流配置已更新: {name}")
    return {"success": True, "data": wf}
