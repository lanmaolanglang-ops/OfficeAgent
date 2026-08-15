"""
Runtime Test Suite - 本地运行时测试
测试: 启动、停止、重启、健康检查、异常恢复
"""
import os
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests.qa_framework import TestCategory, TestSeverity, QATestRunner, get_temp_output_dir


def test_runtime_config():
    """测试Runtime配置"""
    from office_agent.local.runtime import RuntimeConfig, RuntimeManager
    config = RuntimeConfig(host="127.0.0.1", port=18999)
    assert config.host == "127.0.0.1"
    assert config.port == 18999
    assert config.auto_restart is True
    assert config.max_restarts == 5
    return True, "Runtime配置正确"


def test_runtime_port_check():
    """测试端口检测"""
    from office_agent.local.runtime import RuntimeConfig, RuntimeManager
    with tempfile.TemporaryDirectory() as tmpdir:
        config = RuntimeConfig(host="127.0.0.1", port=18998, project_root=tmpdir)
        rm = RuntimeManager(config)
        # 端口应该未被使用
        in_use = rm.is_port_in_use()
        return True, f"端口检测正常 (in_use={in_use})"


def test_runtime_lifecycle():
    """测试Runtime完整生命周期（启动-健康-停止）"""
    from office_agent.local.runtime import RuntimeConfig, RuntimeManager, RuntimeStatus
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建最小化的API
        api_dir = Path(tmpdir) / "office_agent" / "api"
        api_dir.mkdir(parents=True)
        # 创建简单的FastAPI app
        main_py = api_dir / "main.py"
        main_py.write_text('''
from fastapi import FastAPI
app = FastAPI()
@app.get("/health")
def health():
    return {"status": "ok"}
@app.get("/")
def root():
    return {"message": "test"}
''', encoding="utf-8")
        init_py = api_dir / "__init__.py"
        init_py.write_text("", encoding="utf-8")
        pkg_init = Path(tmpdir) / "office_agent" / "__init__.py"
        pkg_init.write_text("", encoding="utf-8")
        config = RuntimeConfig(
            host="127.0.0.1",
            port=18997,
            project_root=tmpdir,
            startup_timeout=15,
            health_check_interval=2,
        )
        rm = RuntimeManager(config)
        try:
            # 启动
            started = rm.start()
            if not started:
                return False, f"启动失败: {rm.state.last_error}"
            assert rm.state.status == RuntimeStatus.RUNNING
            # 健康检查
            health_ok = rm.check_health()
            # 停止
            rm.stop(timeout=5)
            assert rm.state.status == RuntimeStatus.STOPPED
            return health_ok, f"生命周期正常 (health={health_ok})"
        except Exception as e:
            rm.stop(timeout=3)
            raise


def test_runtime_status_report():
    """测试Runtime状态报告"""
    from office_agent.local.runtime import RuntimeConfig, RuntimeManager
    with tempfile.TemporaryDirectory() as tmpdir:
        config = RuntimeConfig(host="127.0.0.1", port=18996, project_root=tmpdir)
        rm = RuntimeManager(config)
        info = rm.get_info()
        assert "status" in info
        assert "url" in info
        assert "config" in info
        assert info["config"]["port"] == 18996
        return True, f"状态报告正常: {info['status']}"


def test_runtime_auto_restart_config():
    """测试自动重启配置"""
    from office_agent.local.runtime import RuntimeConfig, RuntimeManager
    with tempfile.TemporaryDirectory() as tmpdir:
        config = RuntimeConfig(
            host="127.0.0.1", port=18995,
            auto_restart=True, max_restarts=3,
            restart_delay=0.5, project_root=tmpdir,
        )
        rm = RuntimeManager(config)
        assert rm.config.auto_restart is True
        assert rm.config.max_restarts == 3
        return True, "自动重启配置正确"


def run_runtime_tests(runner: QATestRunner):
    """运行所有Runtime测试"""
    print("\n=== Runtime Tests ===")
    runner.run_test("Runtime配置", TestCategory.RUNTIME, TestSeverity.CRITICAL, test_runtime_config)
    runner.run_test("端口检测", TestCategory.RUNTIME, TestSeverity.HIGH, test_runtime_port_check)
    runner.run_test("Runtime生命周期", TestCategory.RUNTIME, TestSeverity.CRITICAL, test_runtime_lifecycle)
    runner.run_test("状态报告", TestCategory.RUNTIME, TestSeverity.MEDIUM, test_runtime_status_report)
    runner.run_test("自动重启配置", TestCategory.RUNTIME, TestSeverity.HIGH, test_runtime_auto_restart_config)


if __name__ == "__main__":
    runner = QATestRunner()
    runner.start()
    run_runtime_tests(runner)
    runner.end()
    summary = runner.get_summary()
    print(f"\nResults: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']}%)")
    for r in runner.get_failed_tests():
        print(f"  FAILED: {r.test_name} - {r.error or r.message}")
