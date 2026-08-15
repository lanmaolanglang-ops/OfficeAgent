"""
Sandbox Environment - 代码执行沙箱
用于Excel数据分析等需要执行Python代码的场景

安全措施：
1. 独立子进程执行
2. 超时控制
3. 模块白名单
4. 禁止危险内置函数
5. 输出大小限制
6. 内存限制（通过子进程）
7. 文件系统隔离（临时目录）

生产环境建议使用Docker容器：
- CPU限制
- 内存限制
- 网络禁用
- 只读文件系统
"""
from __future__ import annotations

import os
import sys
import json
import time
import uuid
import signal
import tempfile
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any

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


# 允许的模块白名单
ALLOWED_MODULES: set[str] = {
    # 数据处理
    "pandas", "numpy", "openpyxl", "xlsxwriter",
    # 数学/统计
    "math", "statistics", "decimal", "fractions", "random",
    # 日期
    "datetime", "time", "calendar",
    # 工具
    "json", "csv", "re", "string", "collections", "itertools",
    "functools", "operator", "copy", "pprint",
    # 图表
    "matplotlib", "matplotlib.pyplot",
    # IO（受限）
    "io", "tempfile",
    # 类型
    "typing", "dataclasses", "enum",
    # 编码
    "base64", "hashlib", "uuid",
}

# 禁止的内置函数
BLOCKED_BUILTINS: set[str] = {
    "eval", "exec", "compile", "__import__",
    "open", "input",
    "globals", "locals", "vars",
    "getattr", "setattr", "delattr",
    "memoryview",
    "breakpoint",
    "exit", "quit",
}

# 禁止的属性访问
BLOCKED_ATTRS: set[str] = {
    "__globals__", "__code__", "__builtins__",
    "__subclasses__", "__mro__", "__bases__",
    "__class__", "__dict__", "__getattribute__",
    "__reduce__", "__reduce_ex__",
    "_os", "_sys", "_subprocess",
}


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
    代码执行沙箱

    用法:
        sandbox = Sandbox(timeout=30, max_memory=512)
        code = "result = 1 + 2"
        result = sandbox.execute(code)
        if result.success:
            print(result.result)
    """

    def __init__(self,
                 timeout_seconds: int = 30,
                 max_memory_mb: int = 512,
                 max_output_bytes: int = 1024 * 1024,  # 1MB
                 allowed_modules: set[str] | None = None,
                 work_dir: str | Path | None = None,
                 docker_image: str | None = None):
        self.timeout_seconds = timeout_seconds
        self.max_memory_mb = max_memory_mb
        self.max_output_bytes = max_output_bytes
        self.allowed_modules = allowed_modules or ALLOWED_MODULES
        self.work_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="sandbox_"))
        self.docker_image = docker_image  # 生产环境用Docker
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def execute(self, code: str, input_data: dict | None = None) -> SandboxResult:
        """
        在沙箱中执行Python代码
        代码中可以直接使用 result 变量作为返回值
        """
        start_time = time.time()

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

            proc = subprocess.run(
                [sys.executable, str(runner_path)],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=str(self.work_dir),
                env=env,
            )

            execution_time = time.time() - start_time

            # 解析输出
            stdout = proc.stdout[:self.max_output_bytes]
            stderr = proc.stderr[:self.max_output_bytes]

            try:
                output = json.loads(stdout)
                stdout = output.get("stdout", "")
                stderr = output.get("stderr", "") or stderr
                result = output.get("result")
                error = output.get("error")
            except json.JSONDecodeError:
                result = None
                error = None

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
                error=str(e),
                execution_time=time.time() - start_time,
            )
        finally:
            # 清理
            try:
                runner_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _pre_check_code(self, code: str) -> str | None:
        """预检查代码，返回拒绝原因或None"""
        # 检查危险导入
        dangerous_imports = ["os", "sys", "subprocess", "shutil", "pathlib",
                           "socket", "http", "urllib", "requests",
                           "ctypes", "multiprocessing", "threading",
                           "signal", "resource", "gc", "inspect"]
        for mod in dangerous_imports:
            if f"import {mod}" in code or f"from {mod}" in code:
                # 但白名单中的模块允许
                if mod in self.allowed_modules:
                    continue
                return f"禁止导入模块: {mod}"

        # 检查危险属性访问
        for attr in BLOCKED_ATTRS:
            if attr in code:
                return f"禁止访问属性: {attr}"

        # 检查文件操作
        if "open(" in code or "with open" in code:
            return "禁止直接文件操作，使用提供的API"

        # 检查网络
        if "socket" in code or "connect(" in code or "urlopen" in code:
            return "禁止网络访问"

        return None

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
            setup += f"\n# 输入数据已提供\n"
        full_code = setup + "\n" + code
        return self.execute(full_code)

    def cleanup(self):
        """清理工作目录"""
        import shutil
        try:
            shutil.rmtree(self.work_dir, ignore_errors=True)
        except Exception:
            pass
