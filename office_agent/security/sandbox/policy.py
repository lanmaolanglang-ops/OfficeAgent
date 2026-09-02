"""Single source of truth for legacy Python subprocess restrictions."""
from __future__ import annotations

import ast


ALLOWED_MODULES: frozenset[str] = frozenset({
    "pandas", "numpy", "openpyxl", "xlsxwriter",
    "math", "statistics", "decimal", "fractions", "random",
    "datetime", "time", "calendar",
    "json", "csv", "re", "string", "collections", "itertools",
    "functools", "operator", "copy", "pprint",
    "matplotlib", "io", "tempfile", "typing", "dataclasses", "enum",
    "base64", "hashlib", "uuid",
})

BLOCKED_BUILTINS: frozenset[str] = frozenset({
    "eval", "exec", "compile", "__import__", "open", "input",
    "globals", "locals", "vars", "getattr", "setattr", "delattr",
    "memoryview", "breakpoint", "exit", "quit",
})

BLOCKED_ATTRS: frozenset[str] = frozenset({
    "__globals__", "__code__", "__builtins__", "__subclasses__", "__mro__",
    "__bases__", "__class__", "__dict__", "__getattribute__", "__reduce__",
    "__reduce_ex__", "_os", "_sys", "_subprocess",
})

BLOCKED_CALL_ATTRIBUTES: frozenset[str] = frozenset({
    "connect", "urlopen", "request", "system", "popen",
})

BLOCKED_NAMES: frozenset[str] = frozenset({"__builtins__", "__loader__"})


def validate_code(code: str, allowed_modules: set[str] | frozenset[str]) -> str | None:
    """Return a stable rejection reason, or ``None`` when AST policy passes."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return f"代码语法错误: {exc.msg}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([alias.name for alias in node.names]
                     if isinstance(node, ast.Import) else [node.module or ""])
            for name in names:
                top = name.split(".")[0]
                if top not in allowed_modules:
                    return f"禁止导入模块: {top}"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_BUILTINS:
                return f"禁止调用内置函数: {node.func.id}"
            if (isinstance(node.func, ast.Attribute)
                    and node.func.attr in BLOCKED_CALL_ATTRIBUTES):
                return f"禁止调用属性: {node.func.attr}"
        elif isinstance(node, ast.Attribute) and node.attr in BLOCKED_ATTRS:
            return f"禁止访问属性: {node.attr}"
        elif isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            return f"禁止访问名称: {node.id}"
    return None
