"""P2-15：``import office_agent.api`` 零副作用（lazy app）回归。

修复前：``api/__init__.py`` 顶部 ``from .main import app, create_app``
导致普通包导入即执行 main.py——构造完整 FastAPI app（19 条路由）、
初始化日志系统、加载 database/config 模块（探针实测 0.79s + 日志系统
初始化 + 多个重模块进入 sys.modules）。修复后包导入轻量化（实测
0.01s、零副作用），``from office_agent.api import app`` 经 PEP 562
模块级 ``__getattr__`` 延迟到首次属性访问，``office_agent.api.main:app``
（uvicorn 与 desktop/app_launcher 的真实入口）行为不变。

子进程隔离：本进程的其它测试可能已加载 api.main，导入副作用必须在
干净解释器中验证。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(code: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        timeout=180, cwd=str(ROOT), env=env,
    )


def test_plain_import_triggers_no_side_effects():
    result = _run(
        "import sys, office_agent.api as api;"
        "assert 'office_agent.api.main' not in sys.modules, 'app 模块被提前加载';"
        "assert 'office_agent.database.session' not in sys.modules, 'database 被提前加载';"
        "assert 'office_agent.config_system.config_manager' not in sys.modules, 'config 被提前加载';"
        "assert 'office_agent.task_queue.worker' not in sys.modules, 'worker 被提前加载';"
        "print('ok')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")
    # 日志系统初始化是 main.py 的模块级副作用，普通包导入不得触发
    assert "日志系统初始化" not in result.stderr


def test_lazy_app_attribute_materializes_main():
    result = _run(
        "import fastapi, sys, office_agent.api as api;"
        "assert isinstance(api.app, fastapi.FastAPI);"
        "assert 'office_agent.api.main' in sys.modules;"
        "assert api.app is api.app, '重复访问必须得到同一对象';"
        "assert callable(api.create_app);"
        "print('ok')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")


def test_explicit_main_import_still_works():
    result = _run(
        "from office_agent.api.main import app;"
        "assert len(app.routes) > 0;"
        "print(type(app).__name__)"
    )
    assert result.returncode == 0, result.stderr
    assert "FastAPI" in result.stdout


def test_dir_lists_lazy_exports_and_unknown_attr_raises():
    code = (
        "import office_agent.api as api\n"
        "listing = dir(api)\n"
        "assert 'app' in listing and 'create_app' in listing\n"
        "try:\n"
        "    api.definitely_not_here\n"
        "except AttributeError:\n"
        "    print('ok')\n"
        "else:\n"
        "    raise SystemExit('unknown attr must raise AttributeError')\n"
    )
    result = _run(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")


def test_repeat_import_is_idempotent():
    result = _run(
        "import office_agent.api, office_agent.api;"
        "import office_agent.api.main as m1;"
        "import office_agent.api.main as m2;"
        "assert m1 is m2;"
        "print('ok')"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ok")
