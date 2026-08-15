"""
API 集成测试
"""
import pytest


class TestAPIApp:
    """API应用测试"""

    def test_api_import(self):
        """测试API模块导入"""
        from office_agent.api.main import app
        assert app is not None

    def test_app_has_routes(self):
        """测试应用有路由"""
        from office_agent.api.main import app
        # FastAPI app有routes属性
        assert hasattr(app, "routes")
        assert len(app.routes) > 0
