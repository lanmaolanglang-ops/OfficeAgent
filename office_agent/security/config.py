"""
Security Configuration - 安全配置
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from ..runtime_config import ALLOWED_UPLOAD_EXTENSIONS


DEFAULT_JWT_SECRET = None


@dataclass
class SecurityConfig:
    """安全配置"""
    # JWT配置
    jwt_secret_key: str | None = field(default_factory=lambda: os.environ.get(
        "OFFICE_AGENT_JWT_SECRET"))
    access_token_expire: int = 3600  # 1小时
    refresh_token_expire: int = 86400 * 7  # 7天

    # 文件安全
    max_file_size: int = 100 * 1024 * 1024  # 100MB
    enable_file_scan: bool = True
    allowed_extensions: tuple[str, ...] = tuple(sorted(ALLOWED_UPLOAD_EXTENSIONS))
    storage_root: str = "./storage/users"

    # 沙箱配置
    sandbox_timeout: int = 30  # 秒
    sandbox_max_output: int = 1024 * 1024  # 1MB
    # 本地子进程不构成 OS 沙箱；在外部隔离执行器落地前保持关闭。
    enable_sandbox: bool = False

    # 登录安全
    max_login_attempts: int = 5
    login_lockout_minutes: int = 15
    password_min_length: int = 8

    # 速率限制
    max_api_calls_per_minute: int = 100
    max_model_calls_per_minute: int = 50
    max_file_uploads_per_hour: int = 50

    # Prompt安全
    enable_prompt_scan: bool = True
    prompt_strict_mode: bool = False

    # 审计
    enable_audit_log: bool = True
    audit_log_retention_days: int = 90

    # API Key
    api_key_expire_days: int = 365

    # CORS：默认只放行本机与 Tauri WebView 来源（"*" 会在启用认证的
    # 部署里把带 Bearer Token 的 API 暴露给任意网站）
    cors_origins: tuple[str, ...] = (
        "http://localhost:1420", "http://127.0.0.1:1420",
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:8765", "http://127.0.0.1:8765",
        "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
    )

    @classmethod
    def from_env(cls) -> "SecurityConfig":
        """从环境变量加载配置"""
        return cls(
            jwt_secret_key=os.environ.get("OFFICE_AGENT_JWT_SECRET"),
            access_token_expire=int(os.environ.get("ACCESS_TOKEN_EXPIRE", "3600")),
            refresh_token_expire=int(os.environ.get("REFRESH_TOKEN_EXPIRE", "604800")),
            max_file_size=int(os.environ.get("MAX_FILE_SIZE", str(100 * 1024 * 1024))),
            enable_file_scan=os.environ.get("ENABLE_FILE_SCAN", "true").lower() == "true",
            sandbox_timeout=int(os.environ.get("SANDBOX_TIMEOUT", "30")),
            enable_sandbox=os.environ.get("ENABLE_SANDBOX", "false").lower() == "true",
            max_login_attempts=int(os.environ.get("MAX_LOGIN_ATTEMPTS", "5")),
            enable_prompt_scan=os.environ.get("ENABLE_PROMPT_SCAN", "true").lower() == "true",
            enable_audit_log=os.environ.get("ENABLE_AUDIT_LOG", "true").lower() == "true",
        )


# 全局配置实例
_config: SecurityConfig | None = None


def get_security_config() -> SecurityConfig:
    """获取安全配置"""
    global _config
    if _config is None:
        _config = SecurityConfig.from_env()
    return _config


def set_security_config(config: SecurityConfig):
    """设置安全配置（测试用）"""
    global _config
    _config = config
