"""
Agent管理路由
集成数据库，从 AgentConfig 表读取配置
"""
import json
from fastapi import APIRouter

from ..schemas.response import AgentInfo, AgentListResponse, BaseResponse, VersionInfo
from ..core.config import settings
from ..core.exceptions import AgentNotFoundError

router = APIRouter(prefix="/api", tags=["Agent管理"])


# 内置Agent（数据库为空时使用）
BUILTIN_AGENTS = [
    {
        "agent_id": "word_agent",
        "name": "Word Agent",
        "agent_type": "word",
        "capabilities": ["排版", "格式转换", "模板套用", "公文生成", "论文排版", "样式统一"],
        "description": "Word文档智能处理Agent，支持自动排版、格式转换、模板套用",
        "status": "available",
        "version": "1.0.0",
    },
    {
        "agent_id": "ppt_agent",
        "name": "PPT Agent",
        "agent_type": "ppt",
        "capabilities": ["生成PPT", "设计模板", "内容规划", "幻灯片制作", "数据可视化"],
        "description": "PPT智能生成Agent，根据大纲自动生成演示文稿",
        "status": "available",
        "version": "1.0.0",
    },
    {
        "agent_id": "excel_agent",
        "name": "Excel Agent",
        "agent_type": "excel",
        "capabilities": ["数据分析", "公式生成", "图表制作", "数据清洗", "统计报表"],
        "description": "Excel智能处理Agent，支持数据分析、图表生成、公式编写",
        "status": "available",
        "version": "1.0.0",
    },
    {
        "agent_id": "orchestrator",
        "name": "Orchestrator",
        "agent_type": "orchestrator",
        "capabilities": ["意图识别", "任务路由", "多Agent协作", "工作流编排"],
        "description": "总控Agent，负责意图识别、任务分发和多Agent协作",
        "status": "available",
        "version": settings.version,
    },
]


def _get_db_session():
    try:
        from ...database.session import SessionLocal
        return SessionLocal()
    except Exception:
        return None


def _ensure_seed_agents(session):
    """确保数据库中有内置 Agent"""
    from ...database.repository import AgentRepository
    repo = AgentRepository(session)
    if repo.count() == 0:
        for a in BUILTIN_AGENTS:
            caps = json.dumps(a["capabilities"], ensure_ascii=False)
            repo.create_agent(
                agent_id=a["agent_id"], name=a["name"], agent_type=a["agent_type"],
                description=a["description"], version=a["version"], capabilities=caps,
            )
        session.commit()


def _db_agent_to_info(db_agent) -> AgentInfo:
    caps = []
    if db_agent.capabilities:
        try:
            caps = json.loads(db_agent.capabilities)
        except (json.JSONDecodeError, TypeError):
            caps = []
    return AgentInfo(
        agent_id=db_agent.agent_id,
        name=db_agent.name,
        agent_type=db_agent.agent_type,
        capabilities=caps,
        description=db_agent.description or "",
        status=db_agent.status or "available",
        version=db_agent.version or "1.0.0",
    )


@router.get("/agents", response_model=BaseResponse[AgentListResponse],
            summary="Agent列表")
async def list_agents():
    """获取当前可用的Agent列表及其能力"""
    session = _get_db_session()
    if session:
        try:
            from ...database.repository import AgentRepository
            _ensure_seed_agents(session)
            repo = AgentRepository(session)
            db_agents = repo.get_enabled()
            if db_agents:
                agents = [_db_agent_to_info(a) for a in db_agents]
                return BaseResponse(data=AgentListResponse(agents=agents, total=len(agents)))
        except Exception:
            pass
        finally:
            session.close()

    # 降级到内置列表
    agents = [AgentInfo(**a) for a in BUILTIN_AGENTS]
    return BaseResponse(data=AgentListResponse(agents=agents, total=len(agents)))


@router.get("/agents/{agent_id}", response_model=BaseResponse[AgentInfo],
            summary="Agent详情")
async def get_agent(agent_id: str):
    """获取指定Agent详情"""
    session = _get_db_session()
    if session:
        try:
            from ...database.repository import AgentRepository
            repo = AgentRepository(session)
            db_agent = repo.get_by_agent_id(agent_id)
            if db_agent:
                return BaseResponse(data=_db_agent_to_info(db_agent))
        except Exception:
            pass
        finally:
            session.close()

    # 降级到内置列表
    for a in BUILTIN_AGENTS:
        if a["agent_id"] == agent_id:
            return BaseResponse(data=AgentInfo(**a))

    raise AgentNotFoundError(f"Agent不存在: {agent_id}")


@router.get("/agents/{agent_id}/versions", response_model=BaseResponse,
            summary="Agent版本列表")
async def list_agent_versions(agent_id: str):
    """获取Agent的版本历史（预留，对接VersionManager）"""
    versions = [
        VersionInfo(
            version="1.0.0",
            name="初始版本",
            status="stable",
            created_at="2026-07-01T00:00:00",
            changelog="初始版本",
        ),
    ]
    return BaseResponse(data={"versions": [v.model_dump() for v in versions]})
