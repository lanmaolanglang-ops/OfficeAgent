"""
API配置
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class APIConfig:
    """API服务配置"""

    # 服务
    host: str = "0.0.0.0"
    port: int = 8765
    debug: bool = False
    title: str = "Office Agent API"
    version: str = "0.50.0"
    description: str = "智能办公自动化Agent统一API服务"

    # 存储
    upload_dir: str = field(default_factory=lambda: os.path.expanduser("~/.office_agent/uploads"))
    output_dir: str = field(default_factory=lambda: os.path.expanduser("~/.office_agent/outputs"))
    max_file_size: int = 100 * 1024 * 1024  # 100MB

    # 允许的文件类型
    allowed_extensions: List[str] = field(default_factory=lambda: [
        ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
        ".pdf", ".txt", ".md",
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    ])

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
        "tauri://localhost", "https://tauri.localhost", "http://tauri.localhost",
    ])
    cors_methods: List[str] = field(default_factory=lambda: ["*"])
    cors_headers: List[str] = field(default_factory=lambda: ["*"])

    # 日志
    log_dir: str = field(default_factory=lambda: os.path.expanduser("~/.office_agent/logs"))
    log_requests: bool = True

    def ensure_dirs(self):
        """确保目录存在"""
        for d in [self.upload_dir, self.output_dir, self.log_dir]:
            os.makedirs(d, exist_ok=True)


# 全局配置实例
settings = APIConfig()
