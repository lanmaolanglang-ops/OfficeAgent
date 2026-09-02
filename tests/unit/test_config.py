"""
Config & Logging 单元测试
"""
import pytest


class TestConfigSystem:
    """配置系统测试"""

    def test_config_import(self):
        """测试配置导入"""
        from office_agent.config_system import ConfigManager
        assert ConfigManager is not None

    def test_security_config(self):
        """测试安全配置"""
        from office_agent.security import SecurityConfig
        config = SecurityConfig()
        assert config.jwt_secret_key is not None
        assert config.access_token_expire > 0
        assert config.max_file_size > 0

    def test_api_auth_configuration_loads_from_environment(self, monkeypatch):
        from office_agent.api.core.config import APIConfig

        monkeypatch.setenv("OFFICE_AGENT_AUTH_ENABLED", "true")
        monkeypatch.setenv("OFFICE_AGENT_API_KEYS", " first , second ")
        config = APIConfig.from_env()
        assert config.auth_enabled is True
        assert config.api_keys == ["first", "second"]

    def test_api_auth_configuration_rejects_lockout(self, monkeypatch):
        from office_agent.api.core.config import APIConfig

        monkeypatch.setenv("OFFICE_AGENT_AUTH_ENABLED", "true")
        monkeypatch.delenv("OFFICE_AGENT_API_KEYS", raising=False)
        monkeypatch.delenv("OFFICE_AGENT_JWT_SECRET", raising=False)
        with pytest.raises(ValueError, match="启用认证"):
            APIConfig.from_env()


class TestLoggingSystem:
    """日志系统测试"""

    def test_logger_import(self):
        """测试日志导入"""
        from office_agent.logging_system import get_logger
        logger = get_logger("test")
        assert logger is not None

    def test_audit_logger(self):
        """测试审计日志"""
        from office_agent.security import get_audit_logger
        audit = get_audit_logger()
        assert audit is not None
        audit.log_login("test_user", success=True, ip="127.0.0.1")
