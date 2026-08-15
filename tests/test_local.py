"""
本地架构测试
测试: 无登录启动、本地文件读写、API Key加密、任务执行、异常恢复、SQLite
"""
import os
import sys
import time
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_local_identity():
    """测试本地身份系统（无登录启动）"""
    from office_agent.local.auth import LocalIdentityManager, AuthMode, AuthAdapter
    with tempfile.TemporaryDirectory() as tmpdir:
        # 身份管理器
        identity = LocalIdentityManager(tmpdir)
        user = identity.initialize()
        assert user.user_id.startswith("local_")
        assert user.username == "local_user"
        assert user.is_local is True
        # 再次加载应该是同一个用户
        user2 = identity.initialize()
        assert user2.user_id == user.user_id
        # 更新设置
        identity.update_settings({"theme": "dark"})
        assert identity.get_setting("theme") == "dark"
        # Auth Adapter - 本地模式
        adapter = AuthAdapter(mode=AuthMode.LOCAL, identity_manager=identity)
        assert adapter.is_local_mode() is True
        result = adapter.authenticate()
        assert result.authenticated is True
        assert result.permissions == ["*"]
        assert adapter.require_auth() is False
        assert adapter.has_permission("anything") is True
        print("  ✓ Local Identity & Auth Adapter")


def test_local_storage():
    """测试本地文件读写"""
    from office_agent.local.storage import LocalFileStorage
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = LocalFileStorage(tmpdir)
        # 保存文件
        info = storage.save_file(b"Hello World", "test.txt", "documents")
        assert info.file_id
        assert info.filename == "test.txt"
        assert info.size == 11
        # 读取文件
        content = storage.read_file(info.file_id)
        assert content == b"Hello World"
        # 列出文件
        files = storage.list_files("documents")
        assert len(files) == 1
        # 存储统计
        stats = storage.get_storage_stats()
        assert stats["documents"]["file_count"] >= 1
        # 删除文件
        assert storage.delete_file(info.file_id) is True
        assert storage.get_file(info.file_id) is None
        print("  ✓ Local File Storage")


def test_sqlite_database():
    """测试SQLite数据库"""
    from office_agent.local.database import LocalDatabase
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = LocalDatabase(db_path)
        # 用户
        db.save_user("test_id", "testuser", "测试用户", {"theme": "dark"})
        user = db.get_user("test_id")
        assert user is not None
        assert user["username"] == "testuser"
        assert user["settings"]["theme"] == "dark"
        # 设置
        db.set_setting("test_key", "test_value")
        assert db.get_setting("test_key") == "test_value"
        db.set_setting("int_key", 42)
        assert db.get_setting("int_key") == 42
        db.set_setting("bool_key", True)
        assert db.get_setting("bool_key") is True
        # 模型配置
        mid = db.save_model_config({
            "provider": "openai",
            "model_name": "gpt-4o",
            "api_key_encrypted": "encrypted_key",
            "is_default": 1,
        })
        assert mid > 0
        configs = db.get_model_configs()
        assert len(configs) >= 1
        # 任务
        db.save_task({"id": "task1", "task_type": "word", "status": "completed"})
        task = db.get_task("task1")
        assert task["task_type"] == "word"
        # 审计日志
        db.add_audit_log("test_action", "target", "details", True)
        logs = db.get_audit_logs(limit=10)
        assert len(logs) >= 1
        db.close()
        print("  ✓ SQLite Database")


def test_credential_manager():
    """测试API Key加密存储"""
    from office_agent.local.credential import LocalCredentialManager
    with tempfile.TemporaryDirectory() as tmpdir:
        cred = LocalCredentialManager(tmpdir)
        # 设置API Key
        cred.set_credential("openai", "api_key", "sk-test-key-1234567890")
        assert cred.has_credential("openai") is True
        # 获取（解密）
        key = cred.get_credential("openai")
        assert key == "sk-test-key-1234567890"
        # 掩码显示
        creds = cred.list_credentials()
        assert len(creds) == 1
        assert "****" in creds[0].masked_value
        assert creds[0].masked_value != "sk-test-key-1234567890"
        # 验证文件不是明文
        cred_file = os.path.join(tmpdir, "credentials.enc")
        assert os.path.exists(cred_file)
        with open(cred_file, "r") as f:
            content = f.read()
        assert "sk-test-key-1234567890" not in content
        # 删除
        assert cred.delete_credential("openai") is True
        assert cred.has_credential("openai") is False
        print("  ✓ Credential Manager (encrypted)")


def test_model_manager():
    """测试模型管理"""
    from office_agent.local.models import ModelManager, ModelProvider
    from office_agent.local.credential import LocalCredentialManager
    with tempfile.TemporaryDirectory() as tmpdir:
        cred = LocalCredentialManager(tmpdir)
        mm = ModelManager(db=None, credential_manager=cred)
        # 预设供应商
        providers = mm.list_providers()
        assert len(providers) >= 8
        # 设置API Key
        mm.set_api_key("openai", "sk-test")
        assert mm.get_api_key("openai") == "sk-test"
        # 启用模型
        mc = mm.enable_model("openai", "gpt-4o", {"is_default": True, "temperature": 0.5})
        assert mc.provider == "openai"
        assert mc.is_default is True
        # 默认模型
        default = mm.get_default_model()
        assert default is not None
        assert default.model_name == "gpt-4o"
        # Fallback链
        mm.enable_model("deepseek", "deepseek-chat", {"is_fallback": True, "priority": 1})
        chain = mm.get_fallback_chain()
        assert len(chain) >= 2
        print("  ✓ Model Manager")


def test_local_task_queue():
    """测试本地任务执行"""
    from office_agent.local.tasks import LocalTaskQueue, TaskStatus
    with tempfile.TemporaryDirectory() as tmpdir:
        queue = LocalTaskQueue(max_workers=2)
        results = []
        def simple_task(x, y):
            time.sleep(0.1)
            return x + y
        def on_done(task):
            results.append(task.result)
        # 提交任务
        task = queue.submit("test", simple_task, args=(2, 3), callback=on_done)
        assert task.status == TaskStatus.PENDING or task.status == TaskStatus.RUNNING
        # 等待完成
        result = queue.wait_for_task(task.task_id, timeout=5)
        assert result.status == TaskStatus.COMPLETED
        assert result.result == 5
        # 统计
        stats = queue.get_stats()
        assert stats["completed"] >= 1
        queue.shutdown()
        print("  ✓ Local Task Queue")


def test_environment_checker():
    """测试环境检测"""
    from office_agent.local.env import EnvironmentChecker, CheckStatus
    checker = EnvironmentChecker()
    report = checker.check_all()
    assert report.python_version
    assert report.platform in ("Windows", "Darwin", "Linux")
    # Python版本应该OK
    py_check = next(c for c in report.checks if c.name == "Python版本")
    assert py_check.status in (CheckStatus.OK, CheckStatus.WARNING, CheckStatus.ERROR)
    print(f"  ✓ Environment Checker (Python {report.python_version}, {report.platform})")


def test_local_config():
    """测试配置管理"""
    from office_agent.local.config import LocalConfigManager
    with tempfile.TemporaryDirectory() as tmpdir:
        config = LocalConfigManager(tmpdir)
        # 默认值
        assert config.get("app.name") == "OfficeAgent"
        # 设置值
        config.set("models.default_model", "gpt-4o")
        assert config.get("models.default_model") == "gpt-4o"
        # 批量更新
        config.update({"app.theme": "dark", "runtime.port": 9000})
        assert config.get("app.theme") == "dark"
        assert config.get("runtime.port") == 9000
        # 重置
        config.reset("app.theme")
        assert config.get("app.theme") == "auto"
        print("  ✓ Local Config Manager")


def test_update_manager():
    """测试版本管理"""
    from office_agent.local.update import UpdateManager
    um = UpdateManager(current_version="0.47.5")
    assert um.get_current_version() == "0.47.5"
    # 版本比较
    assert um.compare_versions("0.48.0", "0.47.5") == 1
    assert um.compare_versions("0.47.0", "0.47.5") == -1
    assert um.compare_versions("0.47.5", "0.47.5") == 0
    print("  ✓ Update Manager")


def test_local_application():
    """测试LocalApplication统一初始化"""
    from office_agent.local import LocalApplication, __version__
    with tempfile.TemporaryDirectory() as tmpdir:
        app = LocalApplication(data_dir=tmpdir)
        app.initialize()
        info = app.get_info()
        assert info["version"] == __version__
        assert info["mode"] == "local"
        assert info["user"]["username"] == "local_user"
        assert info["data_dir"] == tmpdir
        # 各组件已初始化
        assert app.identity is not None
        assert app.auth is not None
        assert app.storage is not None
        assert app.database is not None
        assert app.credentials is not None
        assert app.models is not None
        assert app.tasks is not None
        assert app.config is not None
        app.shutdown()
        print("  ✓ LocalApplication (unified init)")


if __name__ == "__main__":
    print("=" * 50)
    print("OfficeAgent Local Architecture Tests v0.47.5")
    print("=" * 50)
    tests = [
        test_local_identity,
        test_local_storage,
        test_sqlite_database,
        test_credential_manager,
        test_model_manager,
        test_local_task_queue,
        test_environment_checker,
        test_local_config,
        test_update_manager,
        test_local_application,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed == 0:
        print("All local architecture tests passed!")
    sys.exit(0 if failed == 0 else 1)
