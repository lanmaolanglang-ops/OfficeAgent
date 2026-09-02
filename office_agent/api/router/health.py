"""
健康检查路由 - 生产级健康检查
检查 API、数据库、存储、Worker、模型等状态
"""
import os
import time
import platform
import asyncio
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..core.config import settings
from ..core.file_manager import file_manager
from ..schemas.response import BaseResponse

router = APIRouter(tags=["服务状态"])

_start_time = time.time()


def _check_database() -> dict:
    """检查数据库连接"""
    try:
        from office_agent.database.session import SessionLocal
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            session.commit()
            return {"status": "healthy", "message": "连接正常"}
        finally:
            session.close()
    except Exception:
        # 尝试SQLite
        try:
            import sqlite3
            from office_agent.database.connection import DEFAULT_DB_PATH
            db_path = os.environ.get("DB_PATH", str(DEFAULT_DB_PATH))
            conn = sqlite3.connect(db_path)
            conn.execute("SELECT 1")
            conn.close()
            return {"status": "healthy", "message": "SQLite连接正常", "type": "sqlite"}
        except Exception:
            return {"status": "unhealthy", "message": "数据库连接失败"}


def _check_storage() -> dict:
    """检查存储状态"""
    try:
        stats = file_manager.get_storage_stats()
        return {"status": "healthy", "message": "存储正常", **stats}
    except Exception:
        return {"status": "unhealthy", "message": "存储检查失败"}


def _check_workers() -> dict:
    """检查Worker状态"""
    try:
        # 实际任务由 LocalWorker 执行；旧的 API task_manager 仅是数据库不可用时
        # 的兼容回退，用它统计会永远显示 0。
        from office_agent.task_queue import get_worker
        active = get_worker().get_active_count()
        return {
            "status": "healthy",
            "active_tasks": active,
            "message": f"{active}个活跃任务",
        }
    except Exception:
        return {"status": "unknown", "message": "任务引擎状态未知"}


def _check_models() -> dict:
    """检查模型配置（以 ModelManager / models.json 实际配置为准）"""
    try:
        from office_agent.model_gateway import ModelGateway
        gateway = ModelGateway()
        available = gateway.manager.list_available_models()
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
    except Exception:
        return {"status": "error", "model": None, "message": "模型配置检查失败"}

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
    if all(s in ("healthy", "configured", "disabled", "unknown", "template") for s in statuses):
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
async def health():
    """
    健康检查端点 - 用于Docker HEALTHCHECK和K8s liveness/readiness探针
    返回各组件状态和系统资源

    桌面端启动探针只依赖本端点；数据库不可用属于"降级可用"（服务本身可
    响应），返回 200 + status=degraded，避免一次 DB 故障把整个桌面应用卡死
    在启动页。
    """
    uptime = time.time() - _start_time
    database, storage, workers, models, system = await asyncio.gather(
        asyncio.to_thread(_check_database),
        asyncio.to_thread(_check_storage),
        asyncio.to_thread(_check_workers),
        asyncio.to_thread(_check_models),
        asyncio.to_thread(_check_system),
    )
    checks = {
        "api": {"status": "healthy", "message": "API运行中"},
        "database": database,
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
async def health_detail():
    """详细健康检查（含认证）"""
    uptime = time.time() - _start_time
    agents = ["word_agent", "ppt_agent", "excel_agent"]
    database, storage, workers, models, system = await asyncio.gather(
        asyncio.to_thread(_check_database),
        asyncio.to_thread(_check_storage),
        asyncio.to_thread(_check_workers),
        asyncio.to_thread(_check_models),
        asyncio.to_thread(_check_system),
    )
    checks = {
        "api": {"status": "healthy"},
        "database": database,
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
async def readiness():
    """K8s readiness probe - 服务是否准备好接收流量"""
    checks = {"database": await asyncio.to_thread(_check_database)}
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
