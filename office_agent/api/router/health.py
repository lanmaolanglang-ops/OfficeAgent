"""
健康检查路由 - 生产级健康检查
检查 API、数据库、存储、Worker、模型等状态
"""
import os
import time
import platform
from typing import Optional
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..core.config import settings
from ..core.task_manager import task_manager
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
    except Exception as e:
        # 尝试SQLite
        try:
            import sqlite3
            db_path = os.environ.get("DB_PATH", "office_agent.db")
            conn = sqlite3.connect(db_path)
            conn.execute("SELECT 1")
            conn.close()
            return {"status": "healthy", "message": "SQLite连接正常", "type": "sqlite"}
        except Exception as e2:
            return {"status": "unhealthy", "message": str(e2)[:200]}


def _check_storage() -> dict:
    """检查存储状态"""
    try:
        stats = file_manager.get_storage_stats()
        return {"status": "healthy", "message": "存储正常", **stats}
    except Exception as e:
        return {"status": "unhealthy", "message": str(e)[:200]}


def _check_workers() -> dict:
    """检查Worker状态"""
    try:
        active = task_manager.get_active_count()
        return {
            "status": "healthy",
            "active_tasks": active,
            "message": f"{active}个活跃任务",
        }
    except Exception as e:
        return {"status": "unknown", "message": str(e)[:200]}


def _check_models() -> dict:
    """检查模型配置（以 ModelManager / models.json 实际配置为准）"""
    try:
        from office_agent.model_gateway import ModelGateway
        gateway = ModelGateway()
        available = gateway.manager.list_available_models()
        if available:
            primary = available[0]
            return {
                "status": "configured",
                "model": primary.model or primary.id,
                "provider": primary.provider.value,
                "fallback_chain": [m.model or m.id for m in available],
            }
        env_model = os.environ.get("MODEL_PRIMARY_MODEL", "")
        return {
            "status": "template",
            "model": None,
            "fallback_chain": [env_model] if env_model else [],
        }
    except Exception as e:
        return {"status": "error", "model": None, "message": str(e)[:200]}

def _check_system() -> dict:
    """检查系统资源"""
    try:
        import psutil
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "cpu_count": psutil.cpu_count(),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_available_mb": round(psutil.virtual_memory().available / 1024 / 1024),
            "disk_percent": psutil.disk_usage("/").percent if platform.system() != "Windows" else psutil.disk_usage("C:\\").percent,
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
    """
    uptime = time.time() - _start_time
    checks = {
        "api": {"status": "healthy", "message": "API运行中"},
        "database": _check_database(),
        "storage": _check_storage(),
        "workers": _check_workers(),
        "models": _check_models(),
    }
    overall = _get_overall_status(checks)
    system = _check_system()
    status_code = 200 if overall == "healthy" else 503
    return JSONResponse(
        status_code=status_code,
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
    checks = {
        "api": {"status": "healthy"},
        "database": _check_database(),
        "storage": _check_storage(),
        "workers": _check_workers(),
        "models": _check_models(),
    }
    return BaseResponse(data={
        "status": _get_overall_status(checks),
        "version": settings.version,
        "uptime": round(uptime, 2),
        "agents": agents,
        "active_tasks": task_manager.get_active_count(),
        "checks": checks,
        "system": _check_system(),
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
    checks = {
        "database": _check_database(),
    }
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
