"""
RBAC Roles & Permissions - 基于角色的访问控制
定义系统角色和权限
"""
from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field


# ========== 资源类型 ==========
class Resource(str, Enum):
    # 文件资源
    FILE = "file"
    FILE_UPLOAD = "file:upload"
    FILE_DOWNLOAD = "file:download"
    FILE_DELETE = "file:delete"
    # 任务资源
    TASK = "task"
    TASK_CREATE = "task:create"
    TASK_VIEW = "task:view"
    TASK_CANCEL = "task:cancel"
    # Agent资源
    AGENT = "agent"
    AGENT_WORD = "agent:word"
    AGENT_PPT = "agent:ppt"
    AGENT_EXCEL = "agent:excel"
    AGENT_RESEARCH = "agent:research"
    # 工具资源
    TOOL = "tool"
    TOOL_PYTHON = "tool:python"
    TOOL_BROWSER = "tool:browser"
    TOOL_FILE_SYSTEM = "tool:filesystem"
    # 模型资源
    MODEL = "model"
    MODEL_CALL = "model:call"
    # 工作流
    WORKFLOW = "workflow"
    WORKFLOW_CREATE = "workflow:create"
    WORKFLOW_VIEW = "workflow:view"
    # 系统管理
    ADMIN = "admin"
    USER_MANAGE = "admin:user"
    CONFIG = "admin:config"
    AUDIT = "admin:audit"


# ========== 操作类型 ==========
class Action(str, Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXECUTE = "execute"
    ADMIN = "admin"


@dataclass(frozen=True)
class Permission:
    """权限项"""
    resource: str
    action: str
    description: str = ""

    def __str__(self):
        return f"{self.resource}:{self.action}"

    def __hash__(self):
        return hash((self.resource, self.action))

    def __eq__(self, other):
        if isinstance(other, Permission):
            return self.resource == other.resource and self.action == other.action
        if isinstance(other, str):
            return str(self) == other
        return False


# ========== 预定义权限 ==========
PERMISSIONS: dict[str, Permission] = {
    # 文件
    "file:read": Permission("file", "read", "读取文件"),
    "file:write": Permission("file", "write", "上传/创建文件"),
    "file:delete": Permission("file", "delete", "删除文件"),
    "file:download": Permission("file", "download", "下载文件"),
    # 任务
    "task:create": Permission("task", "create", "创建任务"),
    "task:view": Permission("task", "view", "查看任务"),
    "task:cancel": Permission("task", "cancel", "取消任务"),
    # Agent
    "agent:word": Permission("agent", "word", "使用Word Agent"),
    "agent:ppt": Permission("agent", "ppt", "使用PPT Agent"),
    "agent:excel": Permission("agent", "excel", "使用Excel Agent"),
    "agent:research": Permission("agent", "research", "使用Research Agent"),
    # 工具
    "tool:python": Permission("tool", "python", "执行Python代码"),
    "tool:browser": Permission("tool", "browser", "使用浏览器"),
    "tool:filesystem": Permission("tool", "filesystem", "访问文件系统"),
    # 模型
    "model:call": Permission("model", "call", "调用AI模型"),
    # 工作流
    "workflow:create": Permission("workflow", "create", "创建工作流"),
    "workflow:view": Permission("workflow", "view", "查看工作流"),
    # 管理
    "admin:user": Permission("admin", "user", "用户管理"),
    "admin:config": Permission("admin", "config", "系统配置"),
    "admin:audit": Permission("admin", "audit", "查看审计日志"),
}


# ========== 角色定义 ==========
class Role(str, Enum):
    ADMIN = "admin"
    USER = "user"
    GUEST = "guest"


# 角色权限映射
ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.ADMIN: set(PERMISSIONS.keys()),  # 管理员拥有所有权限

    Role.USER: {
        # 文件
        "file:read", "file:write", "file:download",
        # 任务
        "task:create", "task:view", "task:cancel",
        # Agent
        "agent:word", "agent:ppt", "agent:excel", "agent:research",
        # 工具（普通用户不能直接执行Python）
        "tool:browser",
        # 模型
        "model:call",
        # 工作流
        "workflow:create", "workflow:view",
    },

    Role.GUEST: {
        "file:read",
        "task:view",
        "workflow:view",
    },
}


@dataclass
class RoleInfo:
    """角色信息"""
    name: str
    display_name: str
    description: str
    permissions: set[str] = field(default_factory=set)


# 角色详细信息
ROLE_INFO: dict[Role, RoleInfo] = {
    Role.ADMIN: RoleInfo(
        name="admin",
        display_name="管理员",
        description="系统管理员，拥有所有权限",
        permissions=ROLE_PERMISSIONS[Role.ADMIN],
    ),
    Role.USER: RoleInfo(
        name="user",
        display_name="普通用户",
        description="注册用户，可使用所有Office Agent功能",
        permissions=ROLE_PERMISSIONS[Role.USER],
    ),
    Role.GUEST: RoleInfo(
        name="guest",
        display_name="访客",
        description="访客，仅可查看公开内容",
        permissions=ROLE_PERMISSIONS[Role.GUEST],
    ),
}


def get_role_permissions(role: str) -> set[str]:
    """获取角色的权限集合"""
    try:
        role_key = Role(role)
    except ValueError:
        return set()
    return ROLE_PERMISSIONS.get(role_key, set())


def has_permission(role: str, permission: str) -> bool:
    """检查角色是否拥有某权限"""
    perms = get_role_permissions(role)
    return permission in perms


def get_all_permissions() -> dict[str, Permission]:
    """获取所有预定义权限"""
    return dict(PERMISSIONS)


def get_all_roles() -> dict[Role, RoleInfo]:
    """获取所有角色"""
    return dict(ROLE_INFO)
