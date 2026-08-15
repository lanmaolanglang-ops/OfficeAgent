"""
Database 单元测试
"""
import pytest
from pathlib import Path


class TestDatabaseSession:
    """数据库会话测试"""

    def test_session_creation(self):
        """测试会话创建"""
        from office_agent.database.session import SessionLocal
        session = SessionLocal()
        assert session is not None
        session.close()

    def test_session_scope(self):
        """测试会话上下文管理器"""
        from office_agent.database.session import session_scope
        with session_scope() as session:
            assert session is not None


class TestDatabaseModels:
    """数据库模型测试"""

    def test_task_model(self):
        """测试任务模型"""
        from office_agent.database.models import Task
        task = Task(
            id="task_test_001",
            task_type="word_format",
            status="pending",
        )
        assert task.id == "task_test_001"
        assert task.status == "pending"

    def test_file_model(self):
        """测试文件模型"""
        from office_agent.database.models import File
        f = File(
            id="file_test_001",
            filename="test.docx",
            file_type="docx",
        )
        assert f.filename == "test.docx"

    def test_security_models(self):
        """测试安全相关模型"""
        from office_agent.database.models import UserModel, AuditLogModel, APIKeyModel
        user = UserModel(id="user_001", username="testuser", role="user")
        assert user.username == "testuser"
        audit = AuditLogModel(id="audit_001", action="login", user_id="user_001")
        assert audit.action == "login"

    def test_test_result_model(self):
        """测试测试结果模型"""
        from office_agent.database.models import TestResultModel, TestSuiteRunModel
        tr = TestResultModel(
            test_name="test_001",
            test_type="unit",
            module="word",
            status="pass",
        )
        assert tr.test_name == "test_001"
        tsr = TestSuiteRunModel(total=10, passed=9, failed=1)
        assert tsr.total == 10


class TestBaseRepository:
    """基础Repository测试"""

    def test_repository_import(self):
        """测试Repository导入"""
        from office_agent.database.repository.base import BaseRepository
        assert BaseRepository is not None
