"""
API配置
"""
import os
from dataclasses import dataclass, field
from typing import List
from ...runtime_config import (
    ALLOWED_UPLOAD_EXTENSIONS,
    get_log_dir,
    get_output_dir,
    get_upload_dir,
)
from ..._version import __version__

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
    version: str = __version__
    description: str = "智能办公自动化Agent统一API服务"

    # 存储
    upload_dir: str = field(default_factory=lambda: str(get_upload_dir()))
    output_dir: str = field(default_factory=lambda: str(get_output_dir()))
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
    log_dir: str = field(default_factory=lambda: str(get_log_dir()))
    log_requests: bool = True

    # 可信反向代理（IP 或 CIDR）。只有 TCP peer 命中该列表时才消费
    # X-Forwarded-For / X-Real-IP 等转发头，其余情况一律使用 peer 地址，
    # 防止公网客户端伪造身份。
    # 默认仅回环：桌面本地部署的服务只绑定 127.0.0.1，本机代理
    # （如本地开发代理 / 同机网关）是唯一可能合法写入转发头的来源；
    # 该默认值对非本机 peer 完全不生效。暴露到局域网/公网部署时，
    # 必须通过 OFFICE_AGENT_TRUSTED_PROXIES 显式配置真实代理地址。
    trusted_proxies: List[str] = field(
        default_factory=lambda: ["127.0.0.1", "::1"]
    )

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
                "OFFICE_AGENT_UPLOAD_DIR", str(get_upload_dir())
            ),
            output_dir=os.environ.get(
                "OFFICE_AGENT_OUTPUT_DIR", str(get_output_dir())
            ),
            log_dir=str(get_log_dir()),
            auth_enabled=auth_enabled,
            api_keys=api_keys,
            jwt_secret=jwt_secret,
            # 显式设置时整体替换默认回环值，由部署方完全掌控信任边界
            trusted_proxies=(
                _env_list("OFFICE_AGENT_TRUSTED_PROXIES")
                or ["127.0.0.1", "::1"]
            ),
        )


# 全局配置实例
settings = APIConfig.from_env()
