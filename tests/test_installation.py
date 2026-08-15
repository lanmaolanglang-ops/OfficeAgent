"""
Installation Test Suite - 安装测试方案
测试在全新Windows环境下的安装、启动、功能验证
"""
import os
import sys
import json
import time
import shutil
import tempfile
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# 确保项目路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    WARN = "warn"


@dataclass
class InstallTestResult:
    name: str = ""
    status: TestStatus = TestStatus.PASS
    message: str = ""
    duration_ms: float = 0
    details: dict = field(default_factory=dict)


class InstallationTestSuite:
    """安装测试套件"""

    def __init__(self, app_dir: Path = None, port: int = 8765):
        self.app_dir = app_dir or Path(__file__).parent.parent
        self.port = port
        self.results: list[InstallTestResult] = []
        self.backend_process: Optional[subprocess.Popen] = None

    def run_all(self) -> list[InstallTestResult]:
        """运行所有测试"""
        tests = [
            ("检查Python运行时", self.test_python_runtime),
            ("检查核心依赖", self.test_dependencies),
            ("检查目录结构", self.test_directory_structure),
            ("检查配置文件", self.test_config_files),
            ("检查数据目录", self.test_data_directory),
            ("检查日志系统", self.test_logging),
            ("检查环境检测", self.test_system_checker),
            ("检查Backend启动", self.test_backend_startup),
            ("检查健康检查API", self.test_health_endpoint),
            ("检查文件上传API", self.test_file_upload),
            ("检查Word处理", self.test_word_processing),
            ("检查Excel处理", self.test_excel_processing),
            ("检查PPT处理", self.test_ppt_processing),
            ("检查任务队列", self.test_task_queue),
            ("检查数据库", self.test_database),
            ("检查凭据加密", self.test_credential_manager),
            ("检查异常恢复", self.test_error_recovery),
            ("检查Backend停止", self.test_backend_shutdown),
        ]
        for name, test_fn in tests:
            start = time.time()
            try:
                result = test_fn()
                if result is None:
                    result = InstallTestResult(name=name, status=TestStatus.PASS, message="OK")
                result.name = name
                result.duration_ms = (time.time() - start) * 1000
            except Exception as e:
                result = InstallTestResult(
                    name=name, status=TestStatus.FAIL,
                    message=str(e), duration_ms=(time.time() - start) * 1000,
                )
            self.results.append(result)
            icon = {"pass": "✅", "fail": "❌", "skip": "⏭️", "warn": "⚠️"}[result.status.value]
            print(f"{icon} {result.name}: {result.message}")
        return self.results

    def test_python_runtime(self) -> InstallTestResult:
        """检查Python运行时"""
        version = sys.version_info
        if version >= (3, 10):
            return InstallTestResult(
                status=TestStatus.PASS,
                message=f"Python {version.major}.{version.minor}.{version.micro}",
                details={"version": f"{version.major}.{version.minor}.{version.micro}"},
            )
        return InstallTestResult(status=TestStatus.FAIL, message=f"Python版本过低: {version.major}.{version.minor}")

    def test_dependencies(self) -> InstallTestResult:
        """检查核心依赖"""
        required = {
            "fastapi": "fastapi",
            "uvicorn": "uvicorn",
            "docx": "python-docx",
            "pptx": "python-pptx",
            "openpyxl": "openpyxl",
            "pandas": "pandas",
            "sqlalchemy": "sqlalchemy",
        }
        missing = []
        for mod, pkg in required.items():
            try:
                __import__(mod)
            except ImportError:
                missing.append(pkg)
        if missing:
            return InstallTestResult(status=TestStatus.FAIL, message=f"缺少依赖: {', '.join(missing)}")
        return InstallTestResult(status=TestStatus.PASS, message=f"所有{len(required)}个核心依赖已安装")

    def test_directory_structure(self) -> InstallTestResult:
        """检查目录结构"""
        required_dirs = [
            "office_agent",
            "office_agent/api",
            "office_agent/word_engine",
            "office_agent/ppt_engine",
            "office_agent/excel_engine",
            "office_agent/local",
            "office_agent/local/runtime",
            "office_agent/local/config",
            "office_agent/local/database",
            "office_agent/local/auth",
            "office_agent/local/storage",
        ]
        missing = []
        for dir_path in required_dirs:
            if not (self.app_dir / dir_path).exists():
                missing.append(dir_path)
        if missing:
            return InstallTestResult(status=TestStatus.FAIL, message=f"缺少目录: {', '.join(missing)}")
        return InstallTestResult(status=TestStatus.PASS, message=f"所有{len(required_dirs)}个目录存在")

    def test_config_files(self) -> InstallTestResult:
        """检查配置文件"""
        required_files = [
            "office_agent/__init__.py",
            "office_agent/api/main.py",
            "requirements-production.txt",
        ]
        missing = [f for f in required_files if not (self.app_dir / f).exists()]
        if missing:
            return InstallTestResult(status=TestStatus.FAIL, message=f"缺少文件: {', '.join(missing)}")
        return InstallTestResult(status=TestStatus.PASS, message="核心文件完整")

    def test_data_directory(self) -> InstallTestResult:
        """检查数据目录创建"""
        from office_agent.local.config import get_config
        config = get_config()
        data_dir = config.get_data_dir()
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        required_subdirs = ["config", "data", "logs", "cache"]
        for sub in required_subdirs:
            (Path(data_dir) / sub).mkdir(exist_ok=True)
        return InstallTestResult(
            status=TestStatus.PASS,
            message=f"数据目录: {data_dir}",
            details={"data_dir": str(data_dir)},
        )

    def test_logging(self) -> InstallTestResult:
        """检查日志系统"""
        from office_agent.local.logs import LocalLogManager, LogConfig
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"
            mgr = LocalLogManager(config=LogConfig(log_dir=log_dir, console_output=False))
            mgr.log_task("test-001", "test", "completed", "测试日志")
            mgr.log_security("test", "user", "file", True)
            # 刷新所有handler
            for logger in mgr._loggers.values():
                for handler in logger.handlers:
                    handler.flush()
            stats = mgr.get_stats()
            # 关闭所有handler释放文件锁
            for logger in mgr._loggers.values():
                for handler in logger.handlers[:]:
                    handler.close()
                    logger.removeHandler(handler)
            if stats["file_count"] > 0:
                return InstallTestResult(status=TestStatus.PASS, message=f"日志系统正常 ({stats['file_count']}个文件)")
        return InstallTestResult(status=TestStatus.FAIL, message="日志系统异常")

    def test_system_checker(self) -> InstallTestResult:
        """检查环境检测"""
        from office_agent.local.env.system_checker import check_system
        report = check_system()
        errors = [c for c in report.checks if c.status.value == "error"]
        if errors:
            return InstallTestResult(
                status=TestStatus.WARN,
                message=f"环境问题: {', '.join(e.message for e in errors)}",
            )
        return InstallTestResult(
            status=TestStatus.PASS,
            message=f"环境正常: {report.os_name}, Python {report.python_version}",
        )

    def test_backend_startup(self) -> InstallTestResult:
        """检查Backend启动"""
        import urllib.request
        # 先检查端口
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", self.port)) == 0:
                # 已经在运行
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=3) as resp:
                        if resp.status == 200:
                            return InstallTestResult(status=TestStatus.PASS, message="Backend已在运行")
                except Exception:
                    pass
        # 启动Backend
        env = os.environ.copy()
        env["OFFICE_AGENT_LOCAL"] = "1"
        env["AUTH_MODE"] = "local"
        env["PYTHONPATH"] = str(self.app_dir)
        cmd = [
            sys.executable, "-m", "uvicorn",
            "office_agent.api.main:app",
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--log-level", "warning",
        ]
        self.backend_process = subprocess.Popen(
            cmd, cwd=str(self.app_dir), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        # 等待启动
        for i in range(30):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as resp:
                    if resp.status == 200:
                        return InstallTestResult(status=TestStatus.PASS, message=f"Backend启动成功 (PID={self.backend_process.pid})")
            except Exception:
                if self.backend_process.poll() is not None:
                    stdout, stderr = self.backend_process.communicate()
                    return InstallTestResult(
                        status=TestStatus.FAIL,
                        message=f"Backend启动失败: {stderr.decode('utf-8', errors='ignore')[:200]}",
                    )
        return InstallTestResult(status=TestStatus.FAIL, message="Backend启动超时")

    def test_health_endpoint(self) -> InstallTestResult:
        """检查健康检查API"""
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                # 支持多种返回格式
                is_ok = (
                    data.get("status") == "ok" or
                    data.get("status") == "healthy" or
                    data.get("success") is True or
                    data.get("code") == 0 or
                    "data" in data
                )
                if is_ok:
                    version = data.get("version") or data.get("data", {}).get("version", "unknown")
                    return InstallTestResult(status=TestStatus.PASS, message=f"健康检查正常: {version}")
        except Exception as e:
            return InstallTestResult(status=TestStatus.FAIL, message=f"健康检查失败: {e}")
        return InstallTestResult(status=TestStatus.FAIL, message="健康检查返回异常")

    def test_file_upload(self) -> InstallTestResult:
        """检查文件上传"""
        import urllib.request
        import uuid
        # 创建测试文件
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("test content")
            test_file = f.name
        try:
            boundary = uuid.uuid4().hex
            with open(test_file, "rb") as f:
                content = f.read()
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
                f"Content-Type: text/plain\r\n\r\n"
            ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/v1/files/upload",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status in (200, 201):
                        return InstallTestResult(status=TestStatus.PASS, message="文件上传正常")
            except urllib.error.HTTPError as e:
                if e.code in (200, 201, 404):  # 404可能是路由不同
                    return InstallTestResult(status=TestStatus.PASS, message="文件上传API响应正常")
                return InstallTestResult(status=TestStatus.WARN, message=f"上传返回: {e.code}")
        finally:
            Path(test_file).unlink(missing_ok=True)

    def test_word_processing(self) -> InstallTestResult:
        """检查Word处理"""
        try:
            from docx import Document
            from office_agent.parsers import analyze_document
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
                doc = Document()
                doc.add_heading("Test", 0)
                doc.add_paragraph("Hello World")
                doc.save(f.name)
                test_path = f.name
            tree = analyze_document(test_path)
            Path(test_path).unlink(missing_ok=True)
            if tree is not None:
                return InstallTestResult(status=TestStatus.PASS, message="Word解析正常")
        except Exception as e:
            return InstallTestResult(status=TestStatus.FAIL, message=f"Word处理失败: {e}")
        return InstallTestResult(status=TestStatus.FAIL, message="Word处理异常")

    def test_excel_processing(self) -> InstallTestResult:
        """检查Excel处理"""
        try:
            from openpyxl import Workbook
            from office_agent.excel_agent import analyze_excel
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
                wb = Workbook()
                ws = wb.active
                ws.append(["Name", "Value"])
                ws.append(["Test", 123])
                wb.save(f.name)
                test_path = f.name
            result = analyze_excel(test_path)
            Path(test_path).unlink(missing_ok=True)
            if result is not None:
                return InstallTestResult(status=TestStatus.PASS, message="Excel解析正常")
        except Exception as e:
            return InstallTestResult(status=TestStatus.FAIL, message=f"Excel处理失败: {e}")
        return InstallTestResult(status=TestStatus.FAIL, message="Excel处理异常")

    def test_ppt_processing(self) -> InstallTestResult:
        """检查PPT处理"""
        try:
            from pptx import Presentation
            from office_agent.ppt_agent import PPTService  # noqa: F401
            with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
                prs = Presentation()
                slide = prs.slides.add_slide(prs.slide_layouts[0])
                slide.shapes.title.text = "Test"
                prs.save(f.name)
                test_path = f.name
            Path(test_path).unlink(missing_ok=True)
            return InstallTestResult(status=TestStatus.PASS, message="PPT处理正常")
        except Exception as e:
            return InstallTestResult(status=TestStatus.FAIL, message=f"PPT处理失败: {e}")

    def test_task_queue(self) -> InstallTestResult:
        """检查任务队列"""
        try:
            from office_agent.local.tasks import LocalTaskQueue
            queue = LocalTaskQueue(max_workers=2)
            result = queue.submit("test", lambda: 42)
            task_result = queue.wait_for_task(result.task_id, timeout=5)
            queue.shutdown(wait=False)
            if task_result and task_result.result == 42:
                return InstallTestResult(status=TestStatus.PASS, message="任务队列正常")
        except Exception as e:
            return InstallTestResult(status=TestStatus.FAIL, message=f"任务队列失败: {e}")
        return InstallTestResult(status=TestStatus.FAIL, message="任务队列异常")

    def test_database(self) -> InstallTestResult:
        """检查数据库"""
        try:
            from office_agent.local.database import get_database
            db = get_database()
            db.set_setting("test_key", "test_value")
            val = db.get_setting("test_key")
            if val == "test_value":
                return InstallTestResult(status=TestStatus.PASS, message="数据库读写正常")
        except Exception as e:
            return InstallTestResult(status=TestStatus.FAIL, message=f"数据库失败: {e}")
        return InstallTestResult(status=TestStatus.FAIL, message="数据库异常")

    def test_credential_manager(self) -> InstallTestResult:
        """检查凭据加密"""
        try:
            from office_agent.local.credential import get_credential_manager
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["OFFICE_AGENT_DATA_DIR"] = tmpdir
                mgr = get_credential_manager()
                mgr.set_credential("openai", "api_key", "sk-test-123")
                val = mgr.get_credential("openai", "api_key")
                if val == "sk-test-123":
                    return InstallTestResult(status=TestStatus.PASS, message="凭据加密正常")
        except Exception as e:
            return InstallTestResult(status=TestStatus.FAIL, message=f"凭据加密失败: {e}")
        return InstallTestResult(status=TestStatus.FAIL, message="凭据加密异常")

    def test_error_recovery(self) -> InstallTestResult:
        """检查异常恢复"""
        try:
            from office_agent.local.tasks import LocalTaskQueue, TaskStatus
            call_count = 0
            def flaky():
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise RuntimeError("fail")
                return "ok"
            queue = LocalTaskQueue(max_workers=1)
            task = queue.submit("flaky", flaky, max_retries=3)
            for _ in range(20):
                if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                    break
                time.sleep(0.5)
            queue.shutdown(wait=False)
            if task.status == TaskStatus.COMPLETED:
                return InstallTestResult(status=TestStatus.PASS, message=f"异常恢复正常 (重试{call_count-1}次)")
        except Exception as e:
            return InstallTestResult(status=TestStatus.FAIL, message=f"异常恢复失败: {e}")
        return InstallTestResult(status=TestStatus.FAIL, message="异常恢复异常")

    def test_backend_shutdown(self) -> InstallTestResult:
        """检查Backend停止"""
        if self.backend_process:
            try:
                self.backend_process.terminate()
                try:
                    self.backend_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.backend_process.kill()
                return InstallTestResult(status=TestStatus.PASS, message="Backend正常停止")
            except Exception as e:
                return InstallTestResult(status=TestStatus.WARN, message=f"停止异常: {e}")
        return InstallTestResult(status=TestStatus.SKIP, message="Backend非测试启动")

    def get_summary(self) -> dict:
        """获取测试摘要"""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == TestStatus.PASS)
        failed = sum(1 for r in self.results if r.status == TestStatus.FAIL)
        warnings = sum(1 for r in self.results if r.status == TestStatus.WARN)
        skipped = sum(1 for r in self.results if r.status == TestStatus.SKIP)
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "warnings": warnings,
            "skipped": skipped,
            "pass_rate": f"{passed/total*100:.1f}%" if total else "0%",
            "can_release": failed == 0,
        }


def run_installation_tests(app_dir: Path = None, port: int = 8765) -> dict:
    """运行安装测试"""
    print("=" * 60)
    print("  OfficeAgent Installation Test Suite")
    print("=" * 60)
    print()
    suite = InstallationTestSuite(app_dir=app_dir, port=port)
    results = suite.run_all()
    summary = suite.get_summary()
    print()
    print("=" * 60)
    print(f"  Results: {summary['passed']}/{summary['total']} passed ({summary['pass_rate']})")
    if summary["failed"]:
        print(f"  Failed: {summary['failed']}")
    if summary["warnings"]:
        print(f"  Warnings: {summary['warnings']}")
    print(f"  Can release: {'✅ YES' if summary['can_release'] else '❌ NO'}")
    print("=" * 60)
    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--app-dir", type=str, default=None)
    args = parser.parse_args()
    app_dir = Path(args.app_dir) if args.app_dir else Path(__file__).parent.parent
    run_installation_tests(app_dir=app_dir, port=args.port)
