"""
Security & Permission Layer
安全与权限控制系统

包含：
- auth: JWT认证、密码哈希、Token管理
- permission: RBAC权限、访问控制、Agent权限、Tool注册表
- file_security: 文件扫描、用户隔离、安全路径
- prompt: Prompt注入防护、模型安全
- sandbox: 代码执行沙箱
- audit: 安全审计日志
- config: 安全配置
"""
from .config import SecurityConfig, get_security_config, set_security_config
from .auth import (
    hash_password, verify_password, generate_password, is_password_strong,
    JWTManager, TokenPayload,
    TokenManager, TokenType, TokenInfo,
)
from .permission import (
    Role, Resource, Action, Permission,
    PERMISSIONS, ROLE_PERMISSIONS, ROLE_INFO, RoleInfo,
    has_permission, get_role_permissions,
    AccessController, AccessContext, AccessResult, AccessDecision,
    get_access_controller,
    DatabasePermissionResolver, PermissionDecision, seed_default_rbac,
    ToolRegistry, ToolInfo, AgentPermissionManager, RiskLevel,
)
from .file_security import (
    FileScanner, ScanResult, ThreatLevel,
    FileSecurityManager, UserFileSpace,
)
from .prompt import (
    PromptSecurityScanner, ModelSecurityManager,
    SecurityScanResult, InjectionMatch, InjectionType, PromptAction,
)
from .sandbox import (
    Sandbox, SandboxResult, SandboxStatus,
    ALLOWED_MODULES, BLOCKED_BUILTINS,
)
from .audit import (
    AuditLogger, AuditEntry, AuditAction, RiskLevel as AuditRiskLevel,
    get_audit_logger,
)
from .._version import __version__

__all__ = [
    "__version__",
    # 配置
    "SecurityConfig", "get_security_config", "set_security_config",
    # 认证
    "hash_password", "verify_password", "generate_password", "is_password_strong",
    "JWTManager", "TokenPayload",
    "TokenManager", "TokenType", "TokenInfo",
    # 权限
    "Role", "Resource", "Action", "Permission",
    "PERMISSIONS", "ROLE_PERMISSIONS", "ROLE_INFO", "RoleInfo",
    "has_permission", "get_role_permissions",
    "AccessController", "AccessContext", "AccessResult", "AccessDecision",
    "get_access_controller",
    "DatabasePermissionResolver", "PermissionDecision", "seed_default_rbac",
    "ToolRegistry", "ToolInfo", "AgentPermissionManager", "RiskLevel",
    # 文件安全
    "FileScanner", "ScanResult", "ThreatLevel",
    "FileSecurityManager", "UserFileSpace",
    # Prompt安全
    "PromptSecurityScanner", "ModelSecurityManager",
    "SecurityScanResult", "InjectionMatch", "InjectionType", "PromptAction",
    # 沙箱
    "Sandbox", "SandboxResult", "SandboxStatus",
    "ALLOWED_MODULES", "BLOCKED_BUILTINS",
    # 审计
    "AuditLogger", "AuditEntry", "AuditAction", "AuditRiskLevel",
    "get_audit_logger",
]
