"""
Office Agent - Local Desktop Architecture v0.49.0
本地桌面架构模块

包含：
- auth: 本地身份认证（无需登录，Auth Adapter 支持 Local/Cloud 模式）
- storage: 本地文件存储
- database: SQLite 数据库
- credential: API Key 加密管理
- models: 模型管理中心
- tasks: 本地任务队列
- runtime: 运行时进程管理
- config: 本地配置管理
- env: 环境检测
- update: 升级管理
"""

__version__ = "0.49.0"

from pathlib import Path

from .auth import (
    LocalUserProfile, LocalIdentityManager,
    AuthMode, AuthResult, AuthAdapter, get_auth_adapter,
)
from .storage import FileInfo, LocalFileStorage, get_storage
from .database import LocalDatabase, get_database
from .credential import LocalCredentialManager, get_credential_manager
from .models import (
    ModelProvider, ModelConfig, PROVIDER_PRESETS, ModelManager, get_model_manager,
)
from .tasks import TaskStatus, TaskPriority, LocalTask, LocalTaskQueue, get_task_queue
from .runtime import RuntimeConfig, RuntimeManager, get_runtime
from .config import LocalConfigManager, DEFAULT_CONFIG, get_config
from .env import EnvironmentChecker, check_environment
from .update import UpdateManager, CURRENT_VERSION, get_update_manager


class LocalApplication:
    """本地应用统一入口，初始化所有本地组件"""

    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir
        self.identity: LocalIdentityManager = None
        self.auth: AuthAdapter = None
        self.storage: LocalFileStorage = None
        self.database: LocalDatabase = None
        self.credentials: LocalCredentialManager = None
        self.models: ModelManager = None
        self.tasks: LocalTaskQueue = None
        self.runtime: RuntimeManager = None
        self.config: LocalConfigManager = None
        self.updater: UpdateManager = None
        self._initialized = False

    def initialize(self) -> "LocalApplication":
        """Initialize all local application components."""
        if self._initialized:
            return self
        # 配置
        self.config = get_config()
        # 数据目录
        data_dir = self.data_dir or str(self.config.get_data_dir())
        # 身份
        self.identity = LocalIdentityManager(data_dir)
        self.identity.initialize()
        # 认证
        self.auth = get_auth_adapter(mode=AuthMode.LOCAL, identity_manager=self.identity)
        self.auth.authenticate()
        # 存储
        self.storage = get_storage(data_dir)
        # 数据库
        db_path = str(Path(data_dir) / "database" / "officeagent.db") if data_dir else None
        self.database = get_database(db_path)
        # 保存用户到数据库
        user = self.identity.get_current_user()
        self.database.save_user(
            user_id=user.user_id,
            username=user.username,
            display_name=user.display_name,
            settings=user.settings,
            preferences=user.preferences,
        )
        # 凭据
        self.credentials = get_credential_manager()
        # 模型
        self.models = get_model_manager(db=self.database, credential_manager=self.credentials)
        # 任务队列
        max_workers = self.config.get("runtime.max_workers", 4)
        self.tasks = get_task_queue(max_workers=max_workers, db=self.database)
        # 运行时
        runtime_config = RuntimeConfig(
            host=self.config.get("runtime.host", "127.0.0.1"),
            port=self.config.get("runtime.port", 8765),
            workers=1,
            auto_restart=self.config.get("runtime.auto_restart", True),
            project_root=str(Path(__file__).parent.parent.parent),
        )
        self.runtime = get_runtime(runtime_config)
        # 更新
        self.updater = get_update_manager()
        self._initialized = True
        return self

    def get_info(self) -> dict:
        """Return local application information."""
        user = self.identity.get_current_user() if self.identity else None
        return {
            "version": __version__,
            "mode": "local",
            "user": {
                "user_id": user.user_id if user else "",
                "username": user.username if user else "",
                "display_name": user.display_name if user else "",
            },
            "data_dir": str(self.storage.base_dir) if self.storage else "",
            "runtime_url": self.runtime.state.url if self.runtime else "",
            "storage_stats": self.storage.get_storage_stats() if self.storage else {},
        }

    def shutdown(self) -> None:
        """Shutdown local application components."""
        if self.tasks:
            self.tasks.shutdown(wait=False)
        if self.database:
            self.database.close()
        if self.runtime:
            self.runtime.stop()


__all__ = [
    "__version__",
    # Auth
    "LocalUserProfile", "LocalIdentityManager",
    "AuthMode", "AuthResult", "AuthAdapter", "get_auth_adapter",
    # Storage
    "FileInfo", "LocalFileStorage", "get_storage",
    # Database
    "LocalDatabase", "get_database",
    # Credential
    "LocalCredentialManager", "get_credential_manager",
    # Models
    "ModelProvider", "ModelConfig", "PROVIDER_PRESETS", "ModelManager", "get_model_manager",
    # Tasks
    "TaskStatus", "TaskPriority", "LocalTask", "LocalTaskQueue", "get_task_queue",
    # Runtime
    "RuntimeConfig", "RuntimeManager", "get_runtime",
    # Config
    "LocalConfigManager", "DEFAULT_CONFIG", "get_config",
    # Env
    "EnvironmentChecker", "check_environment",
    # Update
    "UpdateManager", "CURRENT_VERSION", "get_update_manager",
    # App
    "LocalApplication",
]
