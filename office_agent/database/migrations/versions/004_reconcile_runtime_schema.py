"""Reconcile Alembic schema with the runtime ORM models.

Revision ID: 004_reconcile_runtime_schema
Revises: 003_file_deleted_at
"""
from alembic import op
import sqlalchemy as sa

revision = "004_reconcile_runtime_schema"
down_revision = "003_file_deleted_at"
branch_labels = None
depends_on = None


_MISSING_TABLES = (
    "agent_runtime_config",
    "error_log",
    "file_rule_config",
    "file_version",
    "model_call_log",
    "model_config",
    "prompt_config",
    "security_api_keys",
    "security_audit_logs",
    "security_login_attempts",
    "security_permissions",
    "security_roles",
    "skill_config",
    "workflow_config",
)


def upgrade() -> None:
    # 这些表在早期 Alembic 基线中完全缺失。使用本版本对应的 ORM
    # Table 定义创建它们，确保约束和索引与运行时一致。
    from office_agent.database.base import Base
    from office_agent.database import models  # noqa: F401

    bind = op.get_bind()
    for table_name in _MISSING_TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)

    with op.batch_alter_table("file") as batch_op:
        batch_op.add_column(sa.Column("storage_path", sa.String(512), nullable=True))
        batch_op.add_column(sa.Column("bucket", sa.String(32), nullable=True, server_default="uploads"))
        batch_op.add_column(sa.Column("storage_backend", sa.String(32), nullable=True, server_default="local"))
        batch_op.add_column(sa.Column("version", sa.Integer(), nullable=True, server_default="1"))
        batch_op.add_column(sa.Column("parent_file_id", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("change_description", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("is_public", sa.Boolean(), nullable=True, server_default=sa.false()))
        batch_op.add_column(sa.Column("access_count", sa.Integer(), nullable=True, server_default="0"))
        batch_op.add_column(sa.Column("last_accessed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(), nullable=True))

    op.execute(sa.text(
        "UPDATE file SET storage_path = file_path WHERE storage_path IS NULL"
    ))
    op.execute(sa.text(
        "UPDATE file SET status = 'ready' WHERE status = 'uploaded'"
    ))
    with op.batch_alter_table("file") as batch_op:
        batch_op.alter_column("storage_path", existing_type=sa.String(512), nullable=False)
        batch_op.alter_column("file_path", existing_type=sa.String(512), nullable=True)
        batch_op.create_index("ix_file_parent_file_id", ["parent_file_id"])

    with op.batch_alter_table("execution_log") as batch_op:
        batch_op.add_column(sa.Column("request_id", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("trace_id", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("span_id", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("parent_span_id", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("input_summary", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("output_summary", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("duration_ms", sa.Integer(), nullable=True, server_default="0"))
        batch_op.add_column(sa.Column("start_time", sa.DateTime(), nullable=True, server_default=sa.func.now()))
        batch_op.add_column(sa.Column("end_time", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_execution_log_request_id", ["request_id"])
        batch_op.create_index("ix_execution_log_trace_id", ["trace_id"])

    op.execute(sa.text(
        "UPDATE execution_log SET duration_ms = execution_time_ms "
        "WHERE duration_ms IS NULL"
    ))
    op.execute(sa.text(
        "UPDATE execution_log SET input_summary = input_text "
        "WHERE input_summary IS NULL"
    ))
    op.execute(sa.text(
        "UPDATE execution_log SET output_summary = output_text "
        "WHERE output_summary IS NULL"
    ))
    with op.batch_alter_table("execution_log") as batch_op:
        batch_op.alter_column("duration_ms", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("start_time", existing_type=sa.DateTime(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("execution_log") as batch_op:
        batch_op.drop_index("ix_execution_log_trace_id")
        batch_op.drop_index("ix_execution_log_request_id")
        for name in (
            "end_time", "start_time", "duration_ms", "output_summary",
            "input_summary", "parent_span_id", "span_id", "trace_id", "request_id",
        ):
            batch_op.drop_column(name)

    with op.batch_alter_table("file") as batch_op:
        batch_op.drop_index("ix_file_parent_file_id")
        for name in (
            "expires_at", "last_accessed_at", "access_count", "is_public",
            "change_description", "parent_file_id", "version", "storage_backend",
            "bucket", "storage_path",
        ):
            batch_op.drop_column(name)

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in reversed(_MISSING_TABLES):
        if table_name in inspector.get_table_names():
            op.drop_table(table_name)
    if "security_users" in sa.inspect(bind).get_table_names():
        op.drop_table("security_users")
