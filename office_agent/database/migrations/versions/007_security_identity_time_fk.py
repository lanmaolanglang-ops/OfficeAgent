"""Unify identity, UTC types and deletion policies.

Revision ID: 007_security_identity_time_fk
Revises: 006_prompt_version_uniqueness
"""
from alembic import op
import sqlalchemy as sa

revision = "007_security_identity_time_fk"
down_revision = "006_prompt_version_uniqueness"
branch_labels = None
depends_on = None

_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _column_is_aware(column_info) -> bool:
    """反射列是否已是 timezone-aware（PG 的 timestamptz）。

    004 修复后全新部署直接创建 aware 列；此时必须跳过
    ``AT TIME ZONE 'UTC'`` 转换——该表达式作用在 timestamptz 上语义相反，
    会把已带时区的数据按会话时区二次偏移。
    """
    return bool(getattr(column_info.get("type"), "timezone", False))


def _drop_fk(batch, table: str, column: str, inspector) -> None:
    for fk in inspector.get_foreign_keys(table):
        if column not in fk.get("constrained_columns", []):
            continue
        referred = fk.get("referred_table") or "unknown"
        name = fk.get("name") or f"fk_{table}_{column}_{referred}"
        batch.drop_constraint(name, type_="foreignkey")


def _copy_legacy_users(bind) -> None:
    inspector = sa.inspect(bind)
    if "security_users" not in inspector.get_table_names():
        return
    rows = bind.execute(sa.text("SELECT * FROM security_users")).mappings().all()
    for row in rows:
        if bind.execute(
            sa.text("SELECT 1 FROM user WHERE id=:id OR username=:username"),
            {"id": row["id"], "username": row["username"]},
        ).first():
            continue
        bind.execute(sa.text(
            "INSERT INTO user (id, username, email, password_hash, display_name, role, "
            "is_active, is_verified, last_login, last_login_ip, failed_login_count, "
            "locked_until, extra, created_at, updated_at) VALUES "
            "(:id, :username, :email, :password_hash, :display_name, :role, :is_active, "
            ":is_verified, :last_login, :last_login_ip, :failed_login_count, "
            ":locked_until, :extra, :created_at, :updated_at)"
        ), {
            "id": row["id"], "username": row["username"], "email": row["email"],
            "password_hash": row["password_hash"],
            "display_name": row["username"], "role": row["role"],
            "is_active": row["is_active"], "is_verified": row["is_verified"],
            "last_login": row["last_login"], "last_login_ip": row["last_login_ip"],
            "failed_login_count": row["failed_login_count"],
            "locked_until": row["locked_until"], "extra": row["extra"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })


def _replace_fk(table: str, column: str, referred: str,
                ondelete: str | None) -> None:
    inspector = sa.inspect(op.get_bind())
    with op.batch_alter_table(table, naming_convention=_NAMING) as batch:
        _drop_fk(batch, table, column, inspector)
        batch.create_foreign_key(
            f"fk_{table}_{column}_{referred}", referred, [column], ["id"],
            ondelete=ondelete,
        )


def upgrade() -> None:
    bind = op.get_bind()
    user_columns = {item["name"] for item in sa.inspect(bind).get_columns("user")}
    additions = {
        "is_verified": sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        "last_login_ip": sa.Column("last_login_ip", sa.String(64), nullable=True),
        "failed_login_count": sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        "locked_until": sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        "extra": sa.Column("extra", sa.JSON(), nullable=True),
    }
    with op.batch_alter_table("user") as batch:
        for name, column in additions.items():
            if name not in user_columns:
                batch.add_column(column)

    _copy_legacy_users(bind)

    _replace_fk("security_audit_logs", "user_id", "user", "SET NULL")
    _replace_fk("security_api_keys", "user_id", "user", "CASCADE")
    _replace_fk("task", "user_id", "user", "SET NULL")
    _replace_fk("task", "parent_task_id", "task", "SET NULL")
    _replace_fk("file", "owner_id", "user", "SET NULL")
    _replace_fk("file_version", "parent_file_id", "file", "CASCADE")
    _replace_fk("execution_log", "task_id", "task", "CASCADE")

    inspector = sa.inspect(bind)
    file_fks = inspector.get_foreign_keys("file")
    if not any("parent_file_id" in fk.get("constrained_columns", []) for fk in file_fks):
        with op.batch_alter_table("file", naming_convention=_NAMING) as batch:
            batch.create_foreign_key(
                "fk_file_parent_file_id_file", "file", ["parent_file_id"], ["id"],
                ondelete="SET NULL",
            )
    for table in ("model_call_log", "error_log"):
        fks = sa.inspect(bind).get_foreign_keys(table)
        if not any("task_id" in fk.get("constrained_columns", []) for fk in fks):
            with op.batch_alter_table(table, naming_convention=_NAMING) as batch:
                batch.create_foreign_key(
                    f"fk_{table}_task_id_task", "task", ["task_id"], ["id"],
                    ondelete="CASCADE",
                )

    aware_columns = {
        "task": ("started_at", "finished_at"),
        "file": ("deleted_at", "last_accessed_at", "expires_at"),
        "execution_log": ("start_time", "end_time"),
        "security_audit_logs": ("timestamp", "created_at", "updated_at"),
        "security_api_keys": ("expires_at", "last_used", "created_at", "updated_at"),
        "security_login_attempts": ("timestamp", "created_at", "updated_at"),
    }
    for table, columns in aware_columns.items():
        existing = {item["name"]: item for item in sa.inspect(bind).get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for column in columns:
                if column in existing and not _column_is_aware(existing[column]):
                    batch.alter_column(
                        column, existing_type=sa.DateTime(),
                        type_=sa.DateTime(timezone=True), existing_nullable=True,
                        postgresql_using=f'"{column}" AT TIME ZONE \'UTC\'',
                    )

    if "security_users" in sa.inspect(bind).get_table_names():
        op.drop_table("security_users")


def downgrade() -> None:
    bind = op.get_bind()
    if "security_users" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "security_users",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("username", sa.String(64), nullable=False, unique=True),
            sa.Column("email", sa.String(128), nullable=True, unique=True),
            sa.Column("password_hash", sa.String(256), nullable=False),
            sa.Column("role", sa.String(32), nullable=False, server_default="user"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_verified", sa.Boolean(), nullable=True),
            sa.Column("last_login", sa.DateTime(), nullable=True),
            sa.Column("last_login_ip", sa.String(64), nullable=True),
            sa.Column("failed_login_count", sa.Integer(), nullable=True),
            sa.Column("locked_until", sa.DateTime(), nullable=True),
            sa.Column("extra", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    referenced_ids = bind.execute(sa.text(
        "SELECT user_id FROM security_api_keys UNION "
        "SELECT user_id FROM security_audit_logs WHERE user_id IS NOT NULL"
    )).scalars().all()
    for user_id in referenced_ids:
        row = bind.execute(sa.text(
            'SELECT * FROM "user" WHERE id=:id'
        ), {"id": user_id}).mappings().first()
        if row is None:
            continue
        bind.execute(sa.text(
            "INSERT INTO security_users (id, username, email, password_hash, role, "
            "is_active, is_verified, last_login, last_login_ip, failed_login_count, "
            "locked_until, extra, created_at, updated_at) VALUES "
            "(:id, :username, :email, :password_hash, :role, :is_active, :is_verified, "
            ":last_login, :last_login_ip, :failed_login_count, :locked_until, :extra, "
            ":created_at, :updated_at)"
        ), {
            "id": row["id"], "username": row["username"], "email": row["email"],
            "password_hash": row["password_hash"] or "", "role": row["role"],
            "is_active": row["is_active"], "is_verified": row["is_verified"],
            "last_login": row["last_login"], "last_login_ip": row["last_login_ip"],
            "failed_login_count": row["failed_login_count"],
            "locked_until": row["locked_until"], "extra": row["extra"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })

    _replace_fk("security_audit_logs", "user_id", "security_users", "SET NULL")
    _replace_fk("security_api_keys", "user_id", "security_users", "CASCADE")

    _replace_fk("task", "user_id", "user", None)
    _replace_fk("file", "owner_id", "user", None)
    _replace_fk("file_version", "parent_file_id", "file", None)
    _replace_fk("execution_log", "task_id", "task", None)

    for table, column in (("model_call_log", "task_id"), ("error_log", "task_id"),
                          ("file", "parent_file_id")):
        inspector = sa.inspect(bind)
        with op.batch_alter_table(table, naming_convention=_NAMING) as batch:
            _drop_fk(batch, table, column, inspector)

    inspector = sa.inspect(bind)
    with op.batch_alter_table("task", naming_convention=_NAMING) as batch:
        _drop_fk(batch, "task", "parent_task_id", inspector)
        batch.create_foreign_key(
            "fk_task_parent_task_id", "task", ["parent_task_id"], ["id"]
        )

    aware_columns = {
        "task": ("started_at", "finished_at"),
        "file": ("deleted_at", "last_accessed_at", "expires_at"),
        "execution_log": ("start_time", "end_time"),
        "security_audit_logs": ("timestamp", "created_at", "updated_at"),
        "security_api_keys": ("expires_at", "last_used", "created_at", "updated_at"),
        "security_login_attempts": ("timestamp", "created_at", "updated_at"),
    }
    for table, columns in aware_columns.items():
        existing = {item["name"]: item for item in sa.inspect(bind).get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for column in columns:
                if column in existing and _column_is_aware(existing[column]):
                    batch.alter_column(
                        column, existing_type=sa.DateTime(timezone=True),
                        type_=sa.DateTime(), existing_nullable=True,
                    )

    legacy_columns = ("extra", "locked_until", "failed_login_count",
                      "last_login_ip", "is_verified")
    with op.batch_alter_table("user") as batch:
        for column in legacy_columns:
            batch.drop_column(column)
