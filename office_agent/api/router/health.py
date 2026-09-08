"""
健康检查路由 - 生产级健康检查
检查 API、数据库、存储、Worker、模型等状态
"""
import os
import time
import platform
import asyncio
import threading
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..core.config import settings
from ..core.file_manager import file_manager
from ..schemas.response import BaseResponse

router = APIRouter(tags=["服务状态"])

_start_time = time.time()


def _check_database(app_state=None) -> dict:
    """检查权威数据库连接，不创建或回退到其他数据库。"""
    if app_state is None or not getattr(app_state, "database_ready", False):
        return {"status": "unhealthy", "message": "数据库启动迁移未完成"}
    try:
        from office_agent.database.session import SessionLocal
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return {"status": "healthy", "message": "连接正常"}
        finally:
            session.close()
    except Exception:
        return {"status": "unhealthy", "message": "数据库连接失败"}


def _check_schema(app_state=None) -> dict:
    """检查 Alembic revision 与任务主链所需的关键列。"""
    if app_state is None or not getattr(app_state, "database_ready", False):
        return {"status": "unhealthy", "message": "数据库 schema 未就绪"}
    try:
        from office_agent.database import migration_head
        from office_agent.database.session import SessionLocal

        expected = migration_head()
        session = SessionLocal()
        try:
            revisions = session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all()
            if revisions != [expected]:
                actual = ",".join(str(item) for item in revisions) or "missing"
                return {
                    "status": "unhealthy",
                    "message": f"数据库 schema 版本不匹配: {actual}",
                    "expected_revision": expected,
                }
            # Zero-row projections fail immediately if an incorrectly-stamped
            # schema lacks columns used by core task and storage requests.
            session.execute(text(
                "SELECT id, status, parent_task_id, revision_number "
                "FROM task WHERE 1=0"
            ))
            session.execute(text(
                "SELECT id, original_name, storage_path, deleted_at "
                "FROM file WHERE 1=0"
            ))
            session.execute(text(
                'SELECT id, is_verified, failed_login_count FROM "user" WHERE 1=0'
            ))
            return {
                "status": "healthy",
                "message": "数据库 schema 已迁移",
                "revision": expected,
            }
        finally:
            session.close()
    except Exception:
        return {"status": "unhealthy", "message": "数据库 schema 检查失败"}


def _check_storage() -> dict:
    """检查存储状态"""
    try:
        stats = file_manager.get_storage_stats()
        return {"status": "healthy", "message": "存储正常", **stats}
    except Exception:
        return {"status": "unhealthy", "message": "存储检查失败"}


def _check_workers(app_state=None) -> dict:
    """检查Worker状态"""
    if app_state is None or not getattr(app_state, "worker_started", False):
        return {"status": "unhealthy", "message": "任务 Worker 未启动"}
    try:
        # 健康探针不得通过 get_worker() 按需构造一个从未启动的实例。
        from office_agent.task_queue import get_initialized_worker
        worker = get_initialized_worker()
        if worker is None or not worker.is_running():
            return {"status": "unhealthy", "message": "任务 Worker 不可用"}
        active = worker.get_active_count()
        return {
            "status": "healthy",
            "active_tasks": active,
            "message": f"{active}个活跃任务",
        }
    except Exception:
        return {"status": "unhealthy", "message": "任务引擎检查失败"}


# ModelGateway() 会构造 ModelManager 并从磁盘读取模型配置与密钥材料。
# 健康检查是高频探针，每次都重建等于把同一份配置反复读盘，还会重复争抢
# 配置写入用的 advisory 文件锁。这里按 TTL 复用实例：读多写少，短暂的配置
# 陈旧对存活探针可接受，TTL 可用环境变量覆盖。
_MODEL_GATEWAY_TTL_SECONDS = float(
    os.environ.get("OFFICE_AGENT_HEALTH_GATEWAY_TTL", "30") or 30
)
_gateway_lock = threading.Lock()
_cached_gateway = None
_cached_gateway_at = 0.0


def _get_cached_gateway():
    """返回带 TTL 的 ModelGateway 实例；TTL 过期后按需重建。"""
    global _cached_gateway, _cached_gateway_at

    now = time.monotonic()
    with _gateway_lock:
        if (_cached_gateway is not None
                and now - _cached_gateway_at < _MODEL_GATEWAY_TTL_SECONDS):
            return _cached_gateway
        # 过期：清空缓存后到锁外重建，避免持锁做磁盘 IO
        _cached_gateway = None
        _cached_gateway_at = 0.0

    from office_agent.model_gateway import ModelGateway
    gateway = ModelGateway()
    with _gateway_lock:
        _cached_gateway = gateway
        _cached_gateway_at = time.monotonic()
    return gateway


def _check_models() -> dict:
    """检查模型配置（以 ModelManager / models.json 实际配置为准）"""
    try:
        from office_agent.model_gateway import ModelGateway

        try:
            gateway = _get_cached_gateway()
            available = gateway.manager.list_available_models()
        except Exception:
            # 缓存实例可能已失效（配置被外部替换等），退化为一次性新建再判定
            gateway = ModelGateway()
            available = gateway.manager.list_available_models()
    except Exception:
        return {"status": "error", "model": None, "message": "模型配置检查失败"}

    if available:
        primary = gateway.manager.get_default_model() or available[0]
        ordered = [primary, *(m for m in available if m.id != primary.id)]
        return {
            "status": "configured",
            "model": primary.model or primary.id,
            "provider": primary.provider.value,
            "fallback_chain": [m.model or m.id for m in ordered],
        }
    env_model = os.environ.get("MODEL_PRIMARY_MODEL", "")
    return {
        "status": "template",
        "model": None,
        "fallback_chain": [env_model] if env_model else [],
    }

def _check_system() -> dict:
    """检查系统资源"""
    try:
        import psutil
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "cpu_count": psutil.cpu_count(),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_available_mb": round(psutil.virtual_memory().available / 1024 / 1024),
            "disk_percent": psutil.disk_usage(settings.output_dir).percent,
        }
    except ImportError:
        return {"status": "psutil not installed"}
    except Exception:
        return {"status": "unknown"}


def _get_overall_status(checks: dict) -> str:
    """计算整体状态"""
    statuses = [v.get("status") for v in checks.values() if isinstance(v, dict)]
    if "unhealthy" in statuses:
        return "degraded"
    if all(s in ("healthy", "configured", "disabled", "template") for s in statuses):
        return "healthy"
    return "degraded"


@router.get("/", response_model=BaseResponse, summary="根路径")
async def root():
    return BaseResponse(
        data={
            "service": settings.title,
            "version": settings.version,
            "docs": "/docs",
            "health": "/health",
            "metrics": "/metrics",
        }
    )


@router.get("/health", summary="健康检查（Docker/K8s探针）")
async def health(request: Request):
    """
    健康检查端点 - 用于Docker HEALTHCHECK和K8s liveness/readiness探针
    返回各组件状态和系统资源

    数据库、schema 或 Worker 不可用时仍返回可诊断的 200 + degraded；桌面
    启动门禁只接受 healthy，K8s 就绪门禁则通过 /ready 返回 503。
    """
    uptime = time.time() - _start_time
    state = request.app.state
    database, schema, storage, workers, models, system = await asyncio.gather(
        asyncio.to_thread(_check_database, state),
        asyncio.to_thread(_check_schema, state),
        asyncio.to_thread(_check_storage),
        asyncio.to_thread(_check_workers, state),
        asyncio.to_thread(_check_models),
        asyncio.to_thread(_check_system),
    )
    checks = {
        "api": {"status": "healthy", "message": "API运行中"},
        "database": database,
        "schema": schema,
        "storage": storage,
        "workers": workers,
        "models": models,
    }
    overall = _get_overall_status(checks)
    return JSONResponse(
        status_code=200,
        content={
            "status": overall,
            "version": settings.version,
            "uptime_seconds": round(uptime, 1),
            "environment": os.environ.get("APP_ENV", "development"),
            "timestamp": time.time(),
            "checks": checks,
            "system": system,
        },
    )


@router.get("/api/health", summary="详细健康检查")
async def health_detail(request: Request):
    """详细健康检查（含认证）"""
    uptime = time.time() - _start_time
    agents = ["word_agent", "ppt_agent", "excel_agent"]
    state = request.app.state
    database, schema, storage, workers, models, system = await asyncio.gather(
        asyncio.to_thread(_check_database, state),
        asyncio.to_thread(_check_schema, state),
        asyncio.to_thread(_check_storage),
        asyncio.to_thread(_check_workers, state),
        asyncio.to_thread(_check_models),
        asyncio.to_thread(_check_system),
    )
    checks = {
        "api": {"status": "healthy"},
        "database": database,
        "schema": schema,
        "storage": storage,
        "workers": workers,
        "models": models,
    }
    return BaseResponse(data={
        "status": _get_overall_status(checks),
        "version": settings.version,
        "uptime": round(uptime, 2),
        "agents": agents,
        "active_tasks": workers.get("active_tasks", 0),
        "checks": checks,
        "system": system,
    })


@router.get("/api/version", response_model=BaseResponse, summary="版本信息")
async def version():
    return BaseResponse(data={
        "version": settings.version,
        "name": settings.title,
        "description": settings.description,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    })


@router.get("/ready", summary="就绪探针")
async def readiness(request: Request):
    """K8s readiness probe - 服务是否准备好接收流量"""
    state = request.app.state
    database, schema, workers = await asyncio.gather(
        asyncio.to_thread(_check_database, state),
        asyncio.to_thread(_check_schema, state),
        asyncio.to_thread(_check_workers, state),
    )
    checks = {"database": database, "schema": schema, "workers": workers}
    overall = _get_overall_status(checks)
    status_code = 200 if overall == "healthy" else 503
    return JSONResponse(
        status_code=status_code,
        content={"ready": overall == "healthy", "checks": checks},
    )


@router.get("/live", summary="存活探针")
async def liveness():
    """K8s liveness probe - 服务是否存活"""
    return JSONResponse(
        status_code=200,
        content={"alive": True, "uptime": round(time.time() - _start_time, 1)},
    )
