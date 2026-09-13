"""
Agent管理路由
集成数据库，从 AgentConfig 表读取配置
"""
import json
import logging
from fastapi import APIRouter

from ..schemas.response import AgentInfo, AgentListResponse, BaseResponse, VersionInfo
from ..core.config import settings
from ..core.exceptions import AgentNotFoundError

router = APIRouter(prefix="/api", tags=["Agent管理"])
logger = logging.getLogger("office_agent.api.agent")


# 内置 Agent（仅在数据库不可用时作为降级清单）
BUILTIN_AGENTS: list[dict] = [
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
        # 降级读取内置清单前必须留下结构化日志：
        # 此前静默 return None，数据库故障在监控上完全不可见。
        logger.exception("创建数据库会话失败，Agent 接口将降级到内置清单")
        return None


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


# P5-15：降级返回的内置清单不是用户配置的 agent 集合，不能对外声称 available。
DEGRADED_AGENT_STATUS = "degraded"


def _disabled_agent_ids_best_effort(session) -> set[str]:
    """尽力读取被禁用的 agent_id 集合，供降级清单过滤。

    降级发生在主查询失败之后；若会话仍可用，就至少尊重运维已显式禁用的
    选择，而不是把全部内置 agent 重新列为可用。这里任何失败都退化为
    "不过滤"，绝不再抛第二个错误（降级路径自身必须可用）。
    """
    if session is None:
        return set()
    try:
        from ...database.repository import AgentRepository
        return AgentRepository(session).get_disabled_agent_ids()
    except Exception:
        logger.warning("降级清单无法读取禁用状态，按未过滤返回", exc_info=True)
        return set()


def _builtin_fallback_agents(disabled_ids: set[str]) -> list[AgentInfo]:
    """构造降级内置清单：过滤已禁用项，并把状态显式标为 degraded。"""
    return [
        AgentInfo(**{**entry, "status": DEGRADED_AGENT_STATUS})
        for entry in BUILTIN_AGENTS
        if entry["agent_id"] not in disabled_ids
    ]


@router.get("/agents", response_model=BaseResponse[AgentListResponse],
            summary="Agent列表")
async def list_agents():
    """获取当前可用的Agent列表及其能力"""
    disabled_ids: set[str] = set()
    session = _get_db_session()
    if session:
        try:
            from ...database.repository import AgentRepository
            repo = AgentRepository(session)
            db_agents = repo.get_enabled()
            agents = [_db_agent_to_info(a) for a in db_agents]
            # An empty database is a valid explicit configuration. Seeding is
            # handled by setup/migrations, never by this read-only endpoint.
            return BaseResponse(data=AgentListResponse(agents=agents, total=len(agents)))
        except Exception:
            logger.exception("读取 Agent 配置失败，降级到内置清单")
            # 降级前尽力读取禁用状态：不把用户已禁用的内置 agent 重新列为可用
            disabled_ids = _disabled_agent_ids_best_effort(session)
        finally:
            session.close()

    # 降级到内置列表：叠加"禁用"过滤，并显式标记 degraded（非静默降级）
    agents = _builtin_fallback_agents(disabled_ids)
    logger.warning(
        "Agent 列表降级：requested=configured_agents effective=builtin_fallback "
        "degraded=true total=%d excluded_disabled=%d", len(agents), len(disabled_ids))
    return BaseResponse(data=AgentListResponse(
        agents=agents, total=len(agents), degraded=True))


@router.get("/agents/{agent_id}", response_model=BaseResponse[AgentInfo],
            summary="Agent详情")
async def get_agent(agent_id: str):
    """获取指定Agent详情"""
    disabled_ids: set[str] = set()
    session = _get_db_session()
    if session:
        try:
            from ...database.repository import AgentRepository
            repo = AgentRepository(session)
            db_agent = repo.get_by_agent_id(agent_id)
            if db_agent:
                return BaseResponse(data=_db_agent_to_info(db_agent))
        except Exception:
            logger.exception("读取 Agent 详情失败，降级到内置清单")
            disabled_ids = _disabled_agent_ids_best_effort(session)
        finally:
            session.close()

    # 降级到内置列表：已禁用的内置 agent 视为不存在，其余显式标记 degraded
    for a in BUILTIN_AGENTS:
        if a["agent_id"] == agent_id:
            if agent_id in disabled_ids:
                logger.warning(
                    "Agent 详情降级：agent_id=%s requested=configured "
                    "effective=disabled_not_found", agent_id)
                raise AgentNotFoundError(f"Agent不存在: {agent_id}")
            logger.warning(
                "Agent 详情降级：agent_id=%s requested=configured "
                "effective=builtin_fallback degraded=true", agent_id)
            return BaseResponse(data=AgentInfo(**{**a, "status": DEGRADED_AGENT_STATUS}))

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
