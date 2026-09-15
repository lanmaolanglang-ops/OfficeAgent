"""
Office Agent API - 统一接口服务层

启动方式：
    uvicorn office_agent.api.main:app --host 0.0.0.0 --port 8765 --reload

或直接运行：
    python -m office_agent.api.main

API文档：
    Swagger UI: http://localhost:8765/docs
    ReDoc: http://localhost:8765/redoc
    Metrics: http://localhost:8765/metrics
"""
import os
import sys
import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from office_agent.api.core.config import settings
from office_agent.api.core.pagination import limit_query
from office_agent.api.deps import get_db_session
from office_agent.api.core.handlers import register_exception_handlers
from office_agent.api.middleware import AuthMiddleware
from office_agent.api.router import (
    health_router, chat_router, file_router, task_router, agent_router,
    config_router,
    settings_router,
    security_router,
    skills_router,
)
from office_agent.database import upgrade_database, DATABASE_URL
from office_agent.runtime_config import get_data_root, get_log_dir

# 初始化日志系统（在创建 app 之前）
from office_agent.logging_system import setup_logging, get_logger
setup_logging(
    log_level=os.environ.get("LOG_LEVEL", "INFO"),
    log_dir=str(get_log_dir()),
    enable_db_logging=os.environ.get("ENABLE_DATABASE_LOG", "true").lower() == "true",
    enable_file_logging=True,
)
logger = get_logger("api")

# 确保目录存在
settings.ensure_dirs()


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        await app_instance.state.startup_handler()
        try:
            yield
        finally:
            await app_instance.state.shutdown_handler()

    app = FastAPI(
        title=settings.title,
        description=settings.description,
        version=settings.version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={
            "name": "Office Agent",
        },
        license_info={
            "name": "Internal",
        },
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials="*" not in settings.cors_origins,
        allow_methods=settings.cors_methods,
        allow_headers=settings.cors_headers + ["X-Request-ID", "X-Trace-ID", "X-User-ID"],
        expose_headers=["X-Request-ID", "X-Trace-ID"],
    )

    # Starlette 中间件语义：后添加的更靠外层、先执行。
    # 下方注册顺序（内 → 外）即实际执行顺序的倒序：
    #   CORS(最内) ← RequestLogging ← RateLimit ← Auth ← LocalGuard
    #   ← ErrorHandling ← RequestSizeLimit(最外)
    # 意图：请求体大小守卫最先廉价拒绝；ErrorHandling 覆盖其内侧所有
    # 中间件层；Auth 先于 RateLimit，使限流可按 user_id 计数（P2-13）；
    # 日志包裹业务路由与 CORS。
    from office_agent.logging_system import (
        RequestLoggingMiddleware, ErrorHandlingMiddleware,
    )
    from office_agent.api.middleware.rate_limit import RateLimitMiddleware
    from office_agent.api.middleware.local_guard import LocalGuardMiddleware
    from office_agent.api.middleware.request_size import RequestSizeLimitMiddleware
    app.add_middleware(RequestLoggingMiddleware)
    # RateLimit 在 Auth 内侧：认证完成后 request.state.user_id 可用
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(LocalGuardMiddleware)
    app.add_middleware(ErrorHandlingMiddleware)
    # 最外层：上传超限请求在认证与 multipart 解析之前按 Content-Length 拒掉，
    # 不读请求体，避免大请求体占用任何下游资源
    app.add_middleware(RequestSizeLimitMiddleware)

    # 注册异常处理器
    register_exception_handlers(app)

    # 注册路由
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(file_router)
    app.include_router(task_router)
    app.include_router(agent_router)
    app.include_router(config_router)
    app.include_router(settings_router)
    app.include_router(security_router)
    app.include_router(skills_router)

    # Metrics 端点
    @app.get("/metrics", summary="Prometheus 指标", tags=["监控"])
    async def metrics():
        from office_agent.logging_system.metrics import registry
        return PlainTextResponse(
            content=registry.render_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/metrics/summary", summary="指标摘要（JSON）", tags=["监控"])
    async def metrics_summary():
        from office_agent.logging_system.metrics import registry
        return JSONResponse(content={"success": True, "data": registry.get_summary()})

    # 日志查询端点
    @app.get("/api/logs/executions", summary="查询执行日志", tags=["监控"])
    def get_execution_logs(task_id: str | None = None, request_id: str | None = None,
                           agent: str | None = None,
                           limit: int = limit_query(),
                           session=Depends(get_db_session)):
        from office_agent.database.repository import ExecutionLogRepository
        repo = ExecutionLogRepository(session)
        if task_id:
            logs = repo.get_by_task(task_id, limit=limit)
        elif request_id:
            logs = repo.get_by_request(request_id)
        elif agent:
            logs = repo.get_by_agent(agent, limit=limit)
        else:
            logs = repo.find(limit=limit, order_by="created_at", descending=True)
        return JSONResponse(content={
            "success": True,
            "data": [_exec_log_to_dict(log) for log in logs[:limit]],
        })

    @app.get("/api/logs/models", summary="查询模型调用日志", tags=["监控"])
    def get_model_logs(task_id: str | None = None, model: str | None = None,
                       limit: int = limit_query(),
                       session=Depends(get_db_session)):
        from office_agent.database.repository import ModelCallLogRepository
        repo = ModelCallLogRepository(session)
        if task_id:
            logs = repo.get_by_task(task_id, limit=limit)
        elif model:
            logs = repo.get_by_model(model, limit=limit)
        else:
            logs = repo.find(limit=limit, order_by="created_at", descending=True)
        return JSONResponse(content={
            "success": True,
            "data": [_model_log_to_dict(log) for log in logs[:limit]],
        })

    @app.get("/api/logs/errors", summary="查询错误日志", tags=["监控"])
    def get_error_logs(limit: int = limit_query(),
                       resolved: bool | None = None,
                       session=Depends(get_db_session)):
        from office_agent.database.repository import ErrorLogRepository
        repo = ErrorLogRepository(session)
        logs = repo.get_recent(limit=limit, resolved=resolved)
        return JSONResponse(content={
            "success": True,
            "data": [_error_log_to_dict(log) for log in logs],
        })

    @app.get("/api/logs/stats", summary="日志统计", tags=["监控"])
    def get_log_stats(hours: int = Query(default=24, ge=1, le=24 * 365),
                      session=Depends(get_db_session)):
        from office_agent.database.repository import (
            ExecutionLogRepository, ModelCallLogRepository, ErrorLogRepository,
        )
        return JSONResponse(content={
            "success": True,
            "data": {
                "executions": ExecutionLogRepository(session).get_stats(hours),
                "model_calls": ModelCallLogRepository(session).get_stats(hours),
                "errors": ErrorLogRepository(session).get_stats(hours),
            },
        })

    @app.get("/api/trace/{trace_id}", summary="查询调用链", tags=["监控"])
    def get_trace(trace_id: str, session=Depends(get_db_session)):
        from office_agent.database.repository import ExecutionLogRepository
        repo = ExecutionLogRepository(session)
        spans = repo.get_by_trace(trace_id)
        return JSONResponse(content={
            "success": True,
            "data": {
                "trace_id": trace_id,
                "spans": [_exec_log_to_dict(s) for s in spans],
            },
        })

    async def on_startup():
        # 桌面模式下写 PID 文件：Tauri 端遇到"端口被占但健康检查失败"的
        # 僵死后端时，可凭该文件识别并接管（仅限本应用进程）
        import sys as _sys
        if os.environ.get("OFFICE_AGENT_LOCAL") == "1" or getattr(_sys, "frozen", False):
            try:
                pid_path = get_data_root() / "backend.pid"
                pid_path.parent.mkdir(parents=True, exist_ok=True)
                pid_path.write_text(str(os.getpid()), encoding="utf-8")
                app.state._pid_file = str(pid_path)
                logger.info(f"Backend PID 文件: {pid_path} (pid={os.getpid()})")
            except Exception as e:
                logger.warning(f"PID 文件写入失败: {e}")

        # 在任何数据库消费者或 Worker 启动前执行权威 Alembic 迁移。
        try:
            schema_revision = upgrade_database()
            app.state.database_ready = True
            app.state.database_schema_revision = schema_revision
            logger.info("数据库: %s (schema=%s)", DATABASE_URL, schema_revision)
        except Exception as e:
            app.state.database_ready = False
            app.state.database_schema_revision = None
            logger.error(f"数据库迁移失败，数据访问将不可用: {e}", exc_info=True)

        # 数据库是任务队列与配置系统的前置条件。失败时保持健康端点可用，
        # 但不启动会持续写库失败的 Worker/调度器。
        if not app.state.database_ready:
            logger.error("以 degraded 模式启动：Worker 与定时调度器未启动")
            return

        async def security_maintenance():
            from office_agent.security.auth import TokenManager
            from office_agent.security import get_audit_logger
            while True:
                await asyncio.sleep(3600)
                await asyncio.to_thread(TokenManager().cleanup_expired)
                await asyncio.to_thread(get_audit_logger().cleanup_expired, 90)

        try:
            from office_agent.security.auth import TokenManager
            from office_agent.security import get_audit_logger
            from office_agent.security.permission import seed_default_rbac
            seed_default_rbac()
            token_manager = TokenManager()
            token_manager.import_legacy_api_keys(settings.api_keys)
            token_manager.cleanup_expired()
            get_audit_logger().cleanup_expired(90)
            app.state.security_maintenance_task = asyncio.create_task(
                security_maintenance(), name="security-maintenance"
            )
        except Exception as e:
            logger.error("安全状态初始化失败: %s", e, exc_info=True)

        # 回收异常中断的任务（进程上次退出时未完成的任务）
        try:
            from office_agent.database.session import SessionLocal
            from office_agent.database.models.task import Task
            from office_agent.database.repository import TaskRepository
            from sqlalchemy import select
            session = SessionLocal()
            try:
                repo = TaskRepository(session)
                stale_tasks = session.scalars(
                    select(Task).where(Task.status.in_(["pending", "queued", "running"]))
                ).all()
                for task in stale_tasks:
                    repo.fail_task(task.id, "任务中断：服务进程在任务执行期间被终止，重启后自动标记为失败")
                session.commit()
                if stale_tasks:
                    logger.info(f"启动恢复：已将 {len(stale_tasks)} 个中断任务标记为失败")
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"启动恢复任务失败: {e}", exc_info=True)

        # 收口永久删除中途留下的 deleting 记录：其物理内容仍占用磁盘，
        # 而记录已对所有查询不可见，没有 GC 就会永久悬挂。
        try:
            from office_agent.storage.storage_service import reconcile_pending_deletions
            outcome = reconcile_pending_deletions()
            if outcome["scanned"]:
                logger.info("启动恢复：已收口 %s 条 deleting 文件记录（失败 %s）",
                            outcome["resolved"], outcome["failed"])
        except Exception as e:
            logger.warning(f"启动恢复 deleting 文件记录失败: {e}", exc_info=True)

        # 初始化配置系统
        try:
            from office_agent.database.session import SessionLocal
            from office_agent.config_system import get_config
            from office_agent.model_gateway.model_manager import ModelManager
            # model_config 表只作旧数据兜底读取；模型配置的读写权威是
            # ModelManager（models.json，与网关/健康检查/设置接口同源）。
            config = get_config(session_factory=SessionLocal, model_store=ModelManager())
            config.initialize(strict=False)
            gc = config.global_config
            summary = config.get_summary()
            logger.info("配置系统: %s 模型, %s Agent, %s Prompt组, 默认模型=%s",
                        summary["models"], summary["agents"], summary["prompt_groups"],
                        gc.default_model)
        except Exception as e:
            logger.warning(f"配置系统初始化失败: {e}", exc_info=True)

        # 初始化任务队列 Worker
        try:
            from office_agent.task_queue import init_worker
            init_worker()
            app.state.worker_started = True
            logger.info("任务队列: local 线程池模式")
        except Exception as e:
            logger.warning(f"任务队列初始化失败: {e}", exc_info=True)

        # 启动定时任务调度器
        try:
            from office_agent.task_queue.scheduler import start_scheduler
            start_scheduler()
            app.state.scheduler_started = True
            logger.info("定时调度器: 已启动")
        except Exception as e:
            logger.warning(f"定时调度器启动失败: {e}", exc_info=True)

        logger.info("=" * 60)
        logger.info(f"Office Agent API v{settings.version} 启动中...")
        logger.info(f"文档地址: http://{settings.host}:{settings.port}/docs")
        logger.info(f"Metrics: http://{settings.host}:{settings.port}/metrics")
        logger.info(f"上传目录: {settings.upload_dir}")
        logger.info(f"认证: {'开启' if settings.auth_enabled else '关闭'}")
        logger.info("=" * 60)

    async def on_shutdown():
        maintenance_task = getattr(app.state, "security_maintenance_task", None)
        if maintenance_task is not None:
            maintenance_task.cancel()
            with suppress(asyncio.CancelledError):
                await maintenance_task
        if getattr(app.state, "scheduler_started", False):
            try:
                from office_agent.task_queue.scheduler import scheduler
                scheduler.stop()
            except Exception:
                logger.warning("定时调度器关闭失败", exc_info=True)
        if getattr(app.state, "worker_started", False):
            try:
                from office_agent.task_queue import get_worker
                get_worker().shutdown(wait=False)
            except Exception:
                logger.warning("任务 Worker 关闭失败", exc_info=True)
        # 审计事件与 API Key 使用统计的落库收尾：必须在数据库引擎
        # 释放之前完成，否则收尾写入会全部失败。
        try:
            from office_agent.security import get_audit_logger
            audit_logger = get_audit_logger()
            if not audit_logger.flush(5.0):
                logger.warning("审计队列关闭冲洗超时，仍有未落库事件")
            audit_logger.close(5.0)
        except Exception:
            logger.warning("审计日志关闭刷新失败", exc_info=True)
        try:
            from office_agent.security.auth.token import flush_all_token_usage
            flushed = flush_all_token_usage()
            if flushed:
                logger.info("已收尾 %s 个 API Key 的使用统计", flushed)
        except Exception:
            logger.warning("API Key 使用统计关闭冲洗失败", exc_info=True)
        try:
            from office_agent.database import dispose_default_engine
            dispose_default_engine()
        except Exception:
            logger.warning("数据库引擎关闭失败", exc_info=True)
        pid_file = getattr(app.state, "_pid_file", None)
        if pid_file:
            try:
                os.remove(pid_file)
            except OSError:
                pass
        logger.info("Office Agent API 已关闭")

    app.state.startup_handler = on_startup
    app.state.shutdown_handler = on_shutdown
    app.state.database_ready = False
    app.state.database_schema_revision = None
    app.state.worker_started = False
    app.state.scheduler_started = False

    return app


def _exec_log_to_dict(log) -> dict:
    return {
        "id": log.id,
        "request_id": log.request_id,
        "task_id": log.task_id,
        "trace_id": log.trace_id,
        "span_id": log.span_id,
        "parent_span_id": log.parent_span_id,
        "agent": log.agent,
        "action": log.action,
        "input_summary": log.input_summary,
        "output_summary": log.output_summary,
        "prompt_tokens": log.prompt_tokens,
        "completion_tokens": log.completion_tokens,
        "total_tokens": log.total_tokens,
        "cost": log.cost,
        "duration_ms": log.duration_ms,
        "status": log.status,
        "error_message": log.error_message,
        "start_time": log.start_time.isoformat() if log.start_time else None,
        "end_time": log.end_time.isoformat() if log.end_time else None,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def _model_log_to_dict(log) -> dict:
    return {
        "id": log.id,
        "request_id": log.request_id,
        "task_id": log.task_id,
        "model_name": log.model_name,
        "provider": log.provider,
        "input_tokens": log.input_tokens,
        "output_tokens": log.output_tokens,
        "total_tokens": log.total_tokens,
        "latency_ms": log.latency_ms,
        "cost_estimate": log.cost_estimate,
        "status": log.status,
        "error_message": log.error_message,
        "retry_count": log.retry_count,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def _error_log_to_dict(log) -> dict:
    return {
        "id": log.id,
        "request_id": log.request_id,
        "task_id": log.task_id,
        "logger_name": log.logger_name,
        "level": log.level,
        "error_type": log.error_type,
        "error_message": log.error_message,
        "stack_trace": log.stack_trace,
        "agent": log.agent,
        "resolved": log.resolved,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


app = create_app()


def run():
    """直接运行"""
    import uvicorn
    uvicorn.run(
        "office_agent.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    run()
