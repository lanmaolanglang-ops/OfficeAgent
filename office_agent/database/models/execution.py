"""执行日志模型"""
import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, BigInteger, ForeignKey, DateTime, Float, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from ..base import Base, TimestampMixin


def _log_uuid():
    return f"log_{uuid.uuid4().hex[:12]}"


def _model_log_uuid():
    return f"mcl_{uuid.uuid4().hex[:12]}"


def _error_uuid():
    return f"err_{uuid.uuid4().hex[:12]}"


class ExecutionLog(Base, TimestampMixin):
    """
    执行日志表

    记录 Agent/Service 每一步执行。
    """
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_log_uuid)

    # 关联
    request_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("task.id"), nullable=True, index=True
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    span_id: Mapped[str] = mapped_column(String(32), nullable=True)
    parent_span_id: Mapped[str] = mapped_column(String(32), nullable=True)

    # 执行主体
    agent: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=True)
    # process/generate/check/call_model 等

    # 输入输出摘要
    input_summary: Mapped[str] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str] = mapped_column(Text, nullable=True)

    # Token和耗时
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    # 时间
    start_time: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # 状态
    status: Mapped[str] = mapped_column(String(32), default="success", index=True)
    # success/error/timeout/running
    error_message: Mapped[str] = mapped_column(Text, nullable=True)

    # 元数据
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)

    # 关系
    task = relationship("Task", back_populates="logs")

    def __repr__(self):
        return f"<ExecutionLog {self.agent} {self.action} {self.status}>"


class ModelCallLog(Base, TimestampMixin):
    """
    模型调用日志表

    记录每次 LLM 调用的详细信息。
    """
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_model_log_uuid)

    # 关联
    request_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    task_id: Mapped[str] = mapped_column(String(32), nullable=True, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)

    # 模型信息
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    # openai/anthropic/doubao/deepseek 等

    # Token
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # 性能
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_estimate: Mapped[float] = mapped_column(Float, default=0.0)

    # 状态
    status: Mapped[str] = mapped_column(String(32), default="success", index=True)
    # success/error/timeout/rate_limited
    error_message: Mapped[str] = mapped_column(Text, nullable=True)

    # 重试信息
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    is_retry: Mapped[bool] = mapped_column(default=False)

    # 元数据
    extra_json: Mapped[str] = mapped_column(Text, nullable=True)

    def __repr__(self):
        return f"<ModelCallLog {self.model_name} {self.total_tokens}tokens {self.status}>"


class ErrorLog(Base, TimestampMixin):
    """
    错误日志表

    记录系统中所有 ERROR 及以上级别的错误。
    """
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_error_uuid)

    # 关联
    request_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    task_id: Mapped[str] = mapped_column(String(32), nullable=True, index=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=True, index=True)

    # 错误信息
    logger_name: Mapped[str] = mapped_column(String(128), nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="ERROR")
    error_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    stack_trace: Mapped[str] = mapped_column(Text, nullable=True)

    # 上下文
    agent: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=True)

    # 处理状态
    resolved: Mapped[bool] = mapped_column(default=False)
    resolution_note: Mapped[str] = mapped_column(Text, nullable=True)

    # 元数据
    extra_json: Mapped[str] = mapped_column(Text, nullable=True)

    def __repr__(self):
        return f"<ErrorLog {self.error_type}: {self.error_message[:50]}>"
