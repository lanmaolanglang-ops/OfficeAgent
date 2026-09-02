"""Fail-closed gate for legacy local Python execution.

A child process, temporary working directory and source filtering are not an
OS security boundary. Production execution is therefore disabled by default.
The explicit ``allow_unsafe_subprocess`` switch exists only for trusted local
tests and development while an external isolated executor is not configured.
"""
from __future__ import annotations

import os
import sys
import json
import time
import uuid
import tempfile
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..error_sanitizer import sanitize_error
from .policy import (
    ALLOWED_MODULES, BLOCKED_ATTRS, BLOCKED_BUILTINS, validate_code,
)

try:
    from office_agent.logging_system import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class SandboxStatus(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    ERROR = "error"
    BLOCKED = "blocked"
    MEMORY_LIMIT = "memory_limit"


@dataclass
class SandboxResult:
    """沙箱执行结果"""
    status: SandboxStatus
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    execution_time: float = 0.0
    output_files: list[str] = field(default_factory=list)
    result: Any = None
    error: str = ""

    @property
    def success(self) -> bool:
        return self.status == SandboxStatus.SUCCESS


SANDBOX_RUNNER_TEMPLATE = '''
import sys
import json
import os
import io
import traceback
import importlib

# 重定向标准输出
stdout_capture = io.StringIO()
stderr_capture = io.StringIO()
old_stdout = sys.stdout
old_stderr = sys.stderr
sys.stdout = stdout_capture
sys.stderr = stderr_capture

result = None
error = None

try:
    # 模块白名单
    ALLOWED_MODULES = {allowed_modules}
    BLOCKED_BUILTINS = {blocked_builtins}
    BLOCKED_ATTRS = {blocked_attrs}

    original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def safe_import(name, *args, **kwargs):
        top = name.split(".")[0]
        if top not in ALLOWED_MODULES:
            raise ImportError(f"模块 '{{name}}' 不在白名单中")
        return original_import(name, *args, **kwargs)

    # 构建安全的builtins
    safe_builtins = dict(__builtins__.__dict__ if hasattr(__builtins__, "__dict__") else __builtins__)
    for name in BLOCKED_BUILTINS:
        safe_builtins.pop(name, None)
    safe_builtins["__import__"] = safe_import

    # 执行代码
    code = compile({code!r}, "<sandbox>", "exec")
    exec_globals = {{"__builtins__": safe_builtins, "__name__": "__sandbox__"}}
    exec(code, exec_globals)

    # 如果定义了main函数，调用它
    if "main" in exec_globals and callable(exec_globals["main"]):
        result = exec_globals["main"]()
    elif "result" in exec_globals:
        result = exec_globals["result"]

except Exception as e:
    error = traceback.format_exc()
finally:
    sys.stdout = old_stdout
    sys.stderr = old_stderr

# 输出结果
output = {{
    "stdout": stdout_capture.getvalue(),
    "stderr": stderr_capture.getvalue(),
    "result": str(result) if result is not None else None,
    "error": error,
}}
sys.stdout.write(json.dumps(output, ensure_ascii=False))
'''


class Sandbox:
    """
    代码执行入口（默认失败关闭）

    用法:
        sandbox = Sandbox()
        code = "result = 1 + 2"
        result = sandbox.execute(code)
        if result.success:
            print(result.result)
    """

    def __init__(self,
                 timeout_seconds: int = 30,
                 max_output_bytes: int = 1024 * 1024,  # 1MB
                 allowed_modules: set[str] | None = None,
                 work_dir: str | Path | None = None,
                 allow_unsafe_subprocess: bool = False):
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.allowed_modules = frozenset(allowed_modules or ALLOWED_MODULES)
        self._temporary_directory = None
        if work_dir is None:
            self._temporary_directory = tempfile.TemporaryDirectory(prefix="sandbox_")
            self.work_dir = Path(self._temporary_directory.name)
        else:
            self.work_dir = Path(work_dir)
        self.allow_unsafe_subprocess = allow_unsafe_subprocess
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def execute(self, code: str, input_data: dict | None = None) -> SandboxResult:
        """
        在沙箱中执行Python代码
        代码中可以直接使用 result 变量作为返回值
        """
        start_time = time.time()

        if not self.allow_unsafe_subprocess:
            return SandboxResult(
                status=SandboxStatus.BLOCKED,
                error=("未配置操作系统级隔离执行器，已拒绝运行代码。"
                       "生产环境请保持 ENABLE_SANDBOX=false。"),
                execution_time=0,
            )

        # 预检查代码
        pre_check = self._pre_check_code(code)
        if pre_check:
            return SandboxResult(
                status=SandboxStatus.BLOCKED,
                error=pre_check,
                execution_time=0,
            )

        # 生成执行脚本
        runner_code = SANDBOX_RUNNER_TEMPLATE.format(
            code=code,
            allowed_modules=repr(self.allowed_modules),
            blocked_builtins=repr(BLOCKED_BUILTINS),
            blocked_attrs=repr(BLOCKED_ATTRS),
        )

        runner_path = self.work_dir / f"runner_{uuid.uuid4().hex[:8]}.py"
        runner_path.write_text(runner_code, encoding="utf-8")

        try:
            # 执行子进程
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
                "SANDBOX_WORKDIR": str(self.work_dir),
            }
            for key in ("SystemRoot", "WINDIR", "TEMP", "TMP"):
                if os.environ.get(key):
                    env[key] = os.environ[key]

            proc = subprocess.run(
                [sys.executable, str(runner_path)],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=str(self.work_dir),
                env=env,
            )

            execution_time = time.time() - start_time

            # runner 输出本身是 JSON。必须先解析完整 JSON，再分别截断其中的
            # stdout/stderr；先截断 JSON 会制造“解析失败但仍返回成功”的假成功。
            raw_stdout = proc.stdout
            raw_stderr = proc.stderr

            try:
                output = json.loads(raw_stdout)
                stdout = str(output.get("stdout", ""))[:self.max_output_bytes]
                stderr = str(output.get("stderr", "") or raw_stderr)[:self.max_output_bytes]
                result = output.get("result")
                error = output.get("error")
            except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                return SandboxResult(
                    status=SandboxStatus.ERROR,
                    stdout=raw_stdout[:self.max_output_bytes],
                    stderr=raw_stderr[:self.max_output_bytes],
                    return_code=proc.returncode,
                    execution_time=execution_time,
                    error=f"沙箱输出格式无效: {exc}",
                )

            if proc.returncode != 0 and not error:
                error = f"沙箱进程异常退出（代码 {proc.returncode}）"

            if error:
                return SandboxResult(
                    status=SandboxStatus.ERROR,
                    stdout=stdout,
                    stderr=stderr,
                    return_code=proc.returncode,
                    execution_time=execution_time,
                    error=error,
                )

            return SandboxResult(
                status=SandboxStatus.SUCCESS,
                stdout=stdout,
                stderr=stderr,
                return_code=proc.returncode,
                execution_time=execution_time,
                result=result,
            )

        except subprocess.TimeoutExpired:
            return SandboxResult(
                status=SandboxStatus.TIMEOUT,
                error=f"执行超时（{self.timeout_seconds}秒）",
                execution_time=self.timeout_seconds,
            )
        except Exception as e:
            return SandboxResult(
                status=SandboxStatus.ERROR,
                error=sanitize_error(e, "代码执行失败"),
                execution_time=time.time() - start_time,
            )
        finally:
            # 清理
            try:
                runner_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _pre_check_code(self, code: str) -> str | None:
        """使用 AST 与运行时白名单同口径预检，拒绝空白/别名绕过。"""
        return validate_code(code, self.allowed_modules)

    def execute_data_analysis(self, code: str, data: Any = None) -> SandboxResult:
        """
        执行数据分析代码
        自动注入pandas和numpy
        """
        setup = """
import pandas as pd
import numpy as np
import json
"""
        if data is not None:
            # 通过 JSON 文本注入，避免 repr 中的自定义对象执行任意代码，且
            # 保证布尔值/null 等跨 Python/JSON 类型可正确还原。
            serialized = json.dumps(data, ensure_ascii=False, default=str)
            setup += f"\ndata = json.loads({serialized!r})\n"
        full_code = setup + "\n" + code
        return self.execute(full_code)

    def cleanup(self):
        """清理工作目录"""
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.cleanup()
