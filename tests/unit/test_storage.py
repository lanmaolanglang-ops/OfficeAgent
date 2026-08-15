"""
Storage 单元测试
"""
import pytest
from pathlib import Path


class TestStorageModule:
    """存储模块测试"""

    def test_storage_import(self):
        """测试存储模块导入"""
        import office_agent.storage as storage
        assert storage is not None

    def test_storage_config(self):
        """测试存储配置"""
        from office_agent.storage import StorageConfig
        config = StorageConfig()
        assert config is not None
