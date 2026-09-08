"""Seed the database-backed RBAC registry.

Revision ID: 008_seed_database_rbac
Revises: 007_security_identity_time_fk
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "008_seed_database_rbac"
down_revision = "007_security_identity_time_fk"
branch_labels = None
depends_on = None

_PERMISSIONS = {
    "file:read": ("file", "read"), "file:write": ("file", "write"),
    "file:delete": ("file", "delete"), "file:download": ("file", "download"),
    "task:create": ("task", "create"), "task:view": ("task", "view"),
    "task:cancel": ("task", "cancel"), "agent:word": ("agent", "word"),
    "agent:ppt": ("agent", "ppt"), "agent:excel": ("agent", "excel"),
    "agent:research": ("agent", "research"), "tool:python": ("tool", "python"),
    "tool:browser": ("tool", "browser"),
    "tool:filesystem": ("tool", "filesystem"), "model:call": ("model", "call"),
    "workflow:create": ("workflow", "create"),
    "workflow:view": ("workflow", "view"), "admin:user": ("admin", "user"),
    "admin:config": ("admin", "config"), "admin:audit": ("admin", "audit"),
}
_USER = {
    "file:read", "file:write", "file:download", "task:create", "task:view",
    "task:cancel", "agent:word", "agent:ppt", "agent:excel", "agent:research",
    "tool:browser", "model:call", "workflow:create", "workflow:view",
}
_ROLES = {
    "admin": ("管理员", set(_PERMISSIONS)),
    "user": ("普通用户", _USER),
    "guest": ("访客", {"file:read", "task:view", "workflow:view"}),
}


def upgrade() -> None:
    bind = op.get_bind()
    seeded_at = datetime.now(timezone.utc)
    permission_table = sa.table(
        "security_permissions",
        sa.column("id", sa.String), sa.column("name", sa.String),
        sa.column("resource", sa.String), sa.column("action", sa.String),
        sa.column("description", sa.Text), sa.column("risk_level", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    role_table = sa.table(
        "security_roles",
        sa.column("id", sa.String), sa.column("name", sa.String),
        sa.column("display_name", sa.String), sa.column("description", sa.Text),
        sa.column("permissions", sa.JSON), sa.column("is_system", sa.Boolean),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    existing_permissions = set(bind.execute(
        sa.select(permission_table.c.name)
    ).scalars())
    permission_rows = [
        {
            "id": f"perm_system_{name.replace(':', '_')}", "name": name,
            "resource": resource, "action": action, "description": name,
            "risk_level": "high" if resource == "admin" else "low",
            # Historical create_all databases made this column NOT NULL but
            # did not give it a server default. Alembic bulk inserts bypass
            # ORM defaults, so seed timestamps explicitly for every schema.
            "created_at": seeded_at, "updated_at": seeded_at,
        }
        for name, (resource, action) in _PERMISSIONS.items()
        if name not in existing_permissions
    ]
    if permission_rows:
        op.bulk_insert(permission_table, permission_rows)
    existing_roles = set(bind.execute(sa.select(role_table.c.name)).scalars())
    role_rows = [
        {
            "id": f"role_system_{name}", "name": name,
            "display_name": display_name, "description": display_name,
            "permissions": sorted(permissions), "is_system": True, "is_active": True,
            "created_at": seeded_at, "updated_at": seeded_at,
        }
        for name, (display_name, permissions) in _ROLES.items()
        if name not in existing_roles
    ]
    if role_rows:
        op.bulk_insert(role_table, role_rows)


def downgrade() -> None:
    role_ids = [f"role_system_{name}" for name in _ROLES]
    permission_ids = [f"perm_system_{name.replace(':', '_')}" for name in _PERMISSIONS]
    op.execute(sa.text(
        "DELETE FROM security_roles WHERE id IN (" +
        ",".join(f"'{value}'" for value in role_ids) + ")"
    ))
    op.execute(sa.text(
        "DELETE FROM security_permissions WHERE id IN (" +
        ",".join(f"'{value}'" for value in permission_ids) + ")"
    ))
