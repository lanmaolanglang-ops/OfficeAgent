"""
Office Agent API Service Layer

FastAPI统一接口服务层，通过HTTP API暴露整个Office Agent能力。
"""
from .main import app, create_app

__version__ = "0.51.1"
__all__ = ["app", "create_app"]
