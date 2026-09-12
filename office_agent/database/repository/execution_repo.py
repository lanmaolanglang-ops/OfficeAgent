"""执行日志 Repository"""
import json
from typing import List
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, and_, func
from sqlalchemy.orm import Session

from .base import BaseRepository
from ..models.execution import ExecutionLog, ModelCallLog, ErrorLog

# 失败口径的单一权威集合：ExecutionLog 的失败终态除 "error" 外还有
# "failed"/"cancelled"（worker/任务层写入），查询统计必须按集合匹配，
# 不能只认字面 "error"（与 ModelCallLog 的 != success 口径对齐）。
FAILED_EXECUTION_STATUSES = ("error", "failed", "cancelled")


class ExecutionLogRepository(BaseRepository[ExecutionLog]):
    def __init__(self, session: Session):
        super().__init__(session, ExecutionLog)

    def get_by_task(self, task_id: str) -> List[ExecutionLog]:
        stmt = select(ExecutionLog).where(
            ExecutionLog.task_id == task_id
        ).order_by(ExecutionLog.start_time.asc())
        return list(self.session.execute(stmt).scalars().all())

    def get_by_request(self, request_id: str) -> List[ExecutionLog]:
        stmt = select(ExecutionLog).where(
            ExecutionLog.request_id == request_id
        ).order_by(ExecutionLog.start_time.asc())
        return list(self.session.execute(stmt).scalars().all())

    def get_by_trace(self, trace_id: str) -> List[ExecutionLog]:
        stmt = select(ExecutionLog).where(
            ExecutionLog.trace_id == trace_id
        ).order_by(ExecutionLog.start_time.asc())
        return list(self.session.execute(stmt).scalars().all())

    def get_by_agent(self, agent: str, limit: int = 100) -> List[ExecutionLog]:
        limit = self._bounded_limit(limit)
        stmt = select(ExecutionLog).where(
            ExecutionLog.agent == agent
        ).order_by(ExecutionLog.created_at.desc()).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def get_errors(self, limit: int = 100) -> List[ExecutionLog]:
        limit = self._bounded_limit(limit)
        stmt = select(ExecutionLog).where(
            ExecutionLog.status.in_(FAILED_EXECUTION_STATUSES)
        ).order_by(ExecutionLog.created_at.desc()).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def log_execution(self, agent: str, action: str | None = None,
                      task_id: str | None = None, request_id: str | None = None,
                      trace_id: str | None = None, span_id: str | None = None,
                      parent_span_id: str | None = None,
                      input_summary: str | None = None, output_summary: str | None = None,
                      prompt_tokens: int = 0, completion_tokens: int = 0,
                      total_tokens: int = 0, cost: float = 0.0,
                      duration_ms: int = 0, status: str = "success",
                      error_message: str | None = None, metadata: dict | None = None,
                      start_time: datetime | None = None, end_time: datetime | None = None) -> ExecutionLog:
        started_at = start_time or datetime.now(timezone.utc)
        terminal_statuses = {"success", "error", "failed", "cancelled", "completed"}
        finished_at = end_time
        if finished_at is None and status in terminal_statuses:
            finished_at = datetime.now(timezone.utc)
        log = ExecutionLog(
            agent=agent, action=action, task_id=task_id,
            request_id=request_id, trace_id=trace_id,
            span_id=span_id, parent_span_id=parent_span_id,
            input_summary=input_summary, output_summary=output_summary,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            total_tokens=total_tokens, cost=cost,
            duration_ms=duration_ms, status=status,
            error_message=error_message,
            metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
            start_time=started_at,
            end_time=finished_at,
        )
        return self.create(log)

    def get_stats(self, hours: int = 24) -> dict:
        """获取执行统计"""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        total = self.session.execute(
            select(func.count(ExecutionLog.id)).where(
                ExecutionLog.created_at >= since
            )
        ).scalar() or 0
        errors = self.session.execute(
            select(func.count(ExecutionLog.id)).where(and_(
                ExecutionLog.created_at >= since,
                ExecutionLog.status.in_(FAILED_EXECUTION_STATUSES),
            ))
        ).scalar() or 0
        avg_duration = self.session.execute(
            select(func.avg(ExecutionLog.duration_ms)).where(
                ExecutionLog.created_at >= since
            )
        ).scalar() or 0
        total_tokens = self.session.execute(
            select(func.coalesce(func.sum(ExecutionLog.total_tokens), 0)).where(
                ExecutionLog.created_at >= since
            )
        ).scalar() or 0
        total_cost = self.session.execute(
            select(func.coalesce(func.sum(ExecutionLog.cost), 0)).where(
                ExecutionLog.created_at >= since
            )
        ).scalar() or 0

        # 按 Agent 统计
        by_agent = {}
        rows = self.session.execute(
            select(ExecutionLog.agent, func.count(ExecutionLog.id)).where(
                ExecutionLog.created_at >= since
            ).group_by(ExecutionLog.agent)
        ).all()
        for agent, count in rows:
            by_agent[agent] = count

        return {
            "total_executions": total,
            "errors": errors,
            "success_rate": round((total - errors) / total * 100, 2) if total else None,
            "avg_duration_ms": round(float(avg_duration), 2),
            "total_tokens": total_tokens,
            "total_cost": round(float(total_cost), 4),
            "by_agent": by_agent,
        }


class ModelCallLogRepository(BaseRepository[ModelCallLog]):
    def __init__(self, session: Session):
        super().__init__(session, ModelCallLog)

    def get_by_task(self, task_id: str) -> List[ModelCallLog]:
        stmt = select(ModelCallLog).where(
            ModelCallLog.task_id == task_id
        ).order_by(ModelCallLog.created_at.asc())
        return list(self.session.execute(stmt).scalars().all())

    def get_by_model(self, model_name: str, limit: int = 100) -> List[ModelCallLog]:
        limit = self._bounded_limit(limit)
        stmt = select(ModelCallLog).where(
            ModelCallLog.model_name == model_name
        ).order_by(ModelCallLog.created_at.desc()).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def log_model_call(self, model_name: str, provider: str | None = None,
                       task_id: str | None = None, request_id: str | None = None,
                       trace_id: str | None = None,
                       input_tokens: int = 0, output_tokens: int = 0,
                       latency_ms: int = 0, cost_estimate: float = 0.0,
                       status: str = "success", error_message: str | None = None,
                       retry_count: int = 0, is_retry: bool = False,
                       extra: dict | None = None) -> ModelCallLog:
        log = ModelCallLog(
            model_name=model_name, provider=provider,
            task_id=task_id, request_id=request_id, trace_id=trace_id,
            input_tokens=input_tokens, output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            latency_ms=latency_ms, cost_estimate=cost_estimate,
            status=status, error_message=error_message,
            retry_count=retry_count, is_retry=is_retry,
            extra_json=json.dumps(extra, ensure_ascii=False) if extra else None,
        )
        return self.create(log)

    def get_stats(self, hours: int = 24) -> dict:
        """获取模型调用统计"""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        total = self.session.execute(
            select(func.count(ModelCallLog.id)).where(
                ModelCallLog.created_at >= since
            )
        ).scalar() or 0
        errors = self.session.execute(
            select(func.count(ModelCallLog.id)).where(and_(
                ModelCallLog.created_at >= since,
                ModelCallLog.status != "success",
            ))
        ).scalar() or 0
        total_input = self.session.execute(
            select(func.coalesce(func.sum(ModelCallLog.input_tokens), 0)).where(
                ModelCallLog.created_at >= since
            )
        ).scalar() or 0
        total_output = self.session.execute(
            select(func.coalesce(func.sum(ModelCallLog.output_tokens), 0)).where(
                ModelCallLog.created_at >= since
            )
        ).scalar() or 0
        total_cost = self.session.execute(
            select(func.coalesce(func.sum(ModelCallLog.cost_estimate), 0)).where(
                ModelCallLog.created_at >= since
            )
        ).scalar() or 0
        avg_latency = self.session.execute(
            select(func.avg(ModelCallLog.latency_ms)).where(
                ModelCallLog.created_at >= since
            )
        ).scalar() or 0

        by_model = {}
        rows = self.session.execute(
            select(ModelCallLog.model_name, func.count(ModelCallLog.id),
                   func.coalesce(func.sum(ModelCallLog.total_tokens), 0)).where(
                ModelCallLog.created_at >= since
            ).group_by(ModelCallLog.model_name)
        ).all()
        for model, count, tokens in rows:
            by_model[model] = {"calls": count, "tokens": int(tokens)}

        return {
            "total_calls": total,
            "errors": errors,
            "success_rate": round((total - errors) / total * 100, 2) if total else None,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "total_cost": round(float(total_cost), 4),
            "avg_latency_ms": round(float(avg_latency), 2),
            "by_model": by_model,
        }


class ErrorLogRepository(BaseRepository[ErrorLog]):
    def __init__(self, session: Session):
        super().__init__(session, ErrorLog)

    def get_recent(self, limit: int = 100, resolved: bool | None = None) -> List[ErrorLog]:
        limit = self._bounded_limit(limit)
        stmt = select(ErrorLog)
        if resolved is not None:
            stmt = stmt.where(ErrorLog.resolved == resolved)
        stmt = stmt.order_by(ErrorLog.created_at.desc()).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def get_by_type(self, error_type: str, limit: int = 100) -> List[ErrorLog]:
        limit = self._bounded_limit(limit)
        stmt = select(ErrorLog).where(
            ErrorLog.error_type == error_type
        ).order_by(ErrorLog.created_at.desc()).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def log_error(self, error_type: str, error_message: str | None = None,
                  stack_trace: str | None = None, request_id: str | None = None,
                  task_id: str | None = None, trace_id: str | None = None,
                  agent: str | None = None, user_id: str | None = None,
                  level: str = "ERROR", logger_name: str | None = None,
                  extra: dict | None = None) -> ErrorLog:
        log = ErrorLog(
            error_type=(error_type or "UnknownError")[:128],
            error_message=(error_message or "")[:2000],
            stack_trace=stack_trace,
            request_id=request_id, task_id=task_id, trace_id=trace_id,
            agent=agent, user_id=user_id,
            level=level, logger_name=logger_name,
            extra_json=json.dumps(extra, ensure_ascii=False) if extra else None,
        )
        return self.create(log)

    def mark_resolved(self, error_id: str, note: str | None = None):
        self.update(error_id, {"resolved": True, "resolution_note": note})

    def get_stats(self, hours: int = 24) -> dict:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        total = self.session.execute(
            select(func.count(ErrorLog.id)).where(
                ErrorLog.created_at >= since
            )
        ).scalar() or 0
        unresolved = self.session.execute(
            select(func.count(ErrorLog.id)).where(and_(
                ErrorLog.created_at >= since,
                ErrorLog.resolved.is_(False),
            ))
        ).scalar() or 0

        by_type = {}
        rows = self.session.execute(
            select(ErrorLog.error_type, func.count(ErrorLog.id)).where(
                ErrorLog.created_at >= since
            ).group_by(ErrorLog.error_type).order_by(func.count(ErrorLog.id).desc())
        ).all()
        for etype, count in rows:
            by_type[etype] = count

        return {
            "total_errors": total,
            "unresolved": unresolved,
            "by_type": by_type,
        }
