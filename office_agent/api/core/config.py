"""
API配置
"""
import os
from dataclasses import dataclass, field
from typing import List
from ...runtime_config import ALLOWED_UPLOAD_EXTENSIONS, get_data_root

# 桌面启动器通过 OFFICE_AGENT_DATA_DIR 把所有本地数据统一重定向到
# %APPDATA%/OfficeAgent；未设置时保持历史行为（~/.office_agent）。
_DATA_ROOT = str(get_data_root())


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是布尔值")


def _env_list(name: str) -> list[str]:
    return [part.strip() for part in os.environ.get(name, "").split(",") if part.strip()]


@dataclass
class APIConfig:
    """API服务配置"""

    # 服务（本地桌面应用只绑定回环地址，避免局域网暴露）
    host: str = "127.0.0.1"
    port: int = 8765
    debug: bool = False
    title: str = "Office Agent API"
    version: str = "0.51.1"
    description: str = "智能办公自动化Agent统一API服务"

    # 存储
    upload_dir: str = field(default_factory=lambda: os.path.join(_DATA_ROOT, "uploads"))
    output_dir: str = field(default_factory=lambda: os.path.join(_DATA_ROOT, "outputs"))
    max_file_size: int = 100 * 1024 * 1024  # 100MB

    # 允许的文件类型
    allowed_extensions: List[str] = field(
        default_factory=lambda: sorted(ALLOWED_UPLOAD_EXTENSIONS)
    )

    # 认证（预留）
    auth_enabled: bool = False
    api_keys: List[str] = field(default_factory=list)
    jwt_secret: str = ""

    # 任务
    task_timeout: int = 300  # 秒
    max_concurrent_tasks: int = 10

    # CORS
    cors_origins: List[str] = field(default_factory=lambda: [
        "http://localhost:1420", "http://127.0.0.1:1420",
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:8765", "http://127.0.0.1:8765",
        "tauri://localhost", "https://tauri.localhost", "http://tauri.localhost",
    ])
    cors_methods: List[str] = field(default_factory=lambda: ["*"])
    cors_headers: List[str] = field(default_factory=lambda: ["*"])

    # 日志
    log_dir: str = field(default_factory=lambda: os.path.join(_DATA_ROOT, "logs"))
    log_requests: bool = True

    def ensure_dirs(self):
        """确保目录存在"""
        for d in [self.upload_dir, self.output_dir, self.log_dir]:
            os.makedirs(d, exist_ok=True)

    @classmethod
    def from_env(cls) -> "APIConfig":
        auth_enabled = _env_bool("OFFICE_AGENT_AUTH_ENABLED", False)
        api_keys = _env_list("OFFICE_AGENT_API_KEYS")
        jwt_secret = os.environ.get("OFFICE_AGENT_JWT_SECRET", "").strip()
        if auth_enabled and not api_keys and not jwt_secret:
            raise ValueError(
                "启用认证时必须配置 OFFICE_AGENT_API_KEYS 或 OFFICE_AGENT_JWT_SECRET"
            )
        if jwt_secret and len(jwt_secret.encode("utf-8")) < 32:
            raise ValueError("OFFICE_AGENT_JWT_SECRET 至少需要 32 字节")
        return cls(
            host=os.environ.get("OFFICE_AGENT_HOST", "127.0.0.1"),
            port=int(os.environ.get("OFFICE_AGENT_PORT", "8765")),
            debug=_env_bool("OFFICE_AGENT_DEBUG", False),
            upload_dir=os.environ.get(
                "OFFICE_AGENT_UPLOAD_DIR", os.path.join(_DATA_ROOT, "uploads")
            ),
            output_dir=os.environ.get(
                "OFFICE_AGENT_OUTPUT_DIR", os.path.join(_DATA_ROOT, "outputs")
            ),
            log_dir=os.environ.get(
                "OFFICE_AGENT_LOG_DIR", os.path.join(_DATA_ROOT, "logs")
            ),
            auth_enabled=auth_enabled,
            api_keys=api_keys,
            jwt_secret=jwt_secret,
        )


# 全局配置实例
settings = APIConfig.from_env()
