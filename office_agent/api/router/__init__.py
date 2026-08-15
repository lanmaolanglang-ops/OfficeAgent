from .health import router as health_router
from .chat import router as chat_router
from .file import router as file_router
from .task import router as task_router
from .agent import router as agent_router
from .config import router as config_router
from .settings import router as settings_router

__all__ = [
    "health_router",
    "chat_router",
    "file_router",
    "task_router",
    "agent_router",
    "config_router",
    "settings_router",
]
