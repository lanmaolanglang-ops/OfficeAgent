"""测试结果模型"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, Text, DateTime

from ..base import Base


def _gen_id():
    return f"test_{uuid.uuid4().hex[:12]}"


class TestResultModel(Base):
    """测试结果表"""
    __tablename__ = "test_results"

    id = Column(String(32), primary_key=True, default=_gen_id)
    test_name = Column(String(255), nullable=False, index=True)
    test_type = Column(String(50), nullable=False, index=True)  # unit/integration/agent/performance
    module = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)  # pass/fail/error/skip
    score = Column(Float, nullable=True)  # Agent评估分数 0-100
    duration = Column(Float, nullable=True)  # 执行时间(秒)
    error_message = Column(Text, nullable=True)
    details = Column(Text, nullable=True)  # JSON详情
    version = Column(String(20), nullable=True)
    branch = Column(String(100), nullable=True)
    commit = Column(String(40), nullable=True)
    ci_run_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "test_name": self.test_name,
            "test_type": self.test_type,
            "module": self.module,
            "status": self.status,
            "score": self.score,
            "duration": self.duration,
            "error_message": self.error_message,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TestSuiteRunModel(Base):
    """测试套件运行记录"""
    __tablename__ = "test_suite_runs"

    id = Column(String(32), primary_key=True, default=_gen_id)
    total = Column(Integer, default=0)
    passed = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    skipped = Column(Integer, default=0)
    pass_rate = Column(Float, default=0)
    total_duration = Column(Float, default=0)
    version = Column(String(20), nullable=True)
    python_version = Column(String(20), nullable=True)
    os_info = Column(String(100), nullable=True)
    ci_run_id = Column(String(100), nullable=True)
    report_path = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "pass_rate": self.pass_rate,
            "total_duration": self.total_duration,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
