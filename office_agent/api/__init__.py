"""
Office Agent API Service Layer

FastAPI统一接口服务层，通过HTTP API暴露整个Office Agent能力。

包导入保持轻量：``import office_agent.api`` 不触发 app 构造、路由装配
与任何启动副作用（日志系统初始化、database/config 模块加载等）；实际
app 由 ``office_agent.api.main:app`` 显式导入（uvicorn 入口与
desktop/app_launcher 均走此路径）。``from office_agent.api import app``
的历史用法经模块级 ``__getattr__`` 延迟到首次属性访问，行为不变。
"""
from .._version import __version__

_LAZY_EXPORTS = ("app", "create_app")


def __getattr__(name: str):
    if name in _LAZY_EXPORTS:
        from . import main as _main
        return getattr(_main, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals().keys(), *_LAZY_EXPORTS})


__all__ = ["app", "create_app", "__version__"]
