"""Permission Module - RBAC权限控制"""
from .roles import (
    Role, Resource, Action, Permission,
    PERMISSIONS, ROLE_PERMISSIONS, ROLE_INFO, RoleInfo,
    has_permission, get_role_permissions, get_all_permissions, get_all_roles,
)
from .access_control import (
    AccessController, AccessContext, AccessResult,
    AccessDecision, get_access_controller,
)
from .agent_permissions import (
    ToolRegistry, ToolInfo, AgentPermissionManager,
    RiskLevel, DEFAULT_TOOLS, AGENT_TOOL_PERMISSIONS,
)

__all__ = [
    "Role", "Resource", "Action", "Permission",
    "PERMISSIONS", "ROLE_PERMISSIONS", "ROLE_INFO", "RoleInfo",
    "has_permission", "get_role_permissions", "get_all_permissions", "get_all_roles",
    "AccessController", "AccessContext", "AccessResult",
    "AccessDecision", "get_access_controller",
    "ToolRegistry", "ToolInfo", "AgentPermissionManager",
    "RiskLevel", "DEFAULT_TOOLS", "AGENT_TOOL_PERMISSIONS",
]
