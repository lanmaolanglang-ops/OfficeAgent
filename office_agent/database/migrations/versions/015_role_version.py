"""Add optimistic-lock version on security_roles.

Revision ID: 015_role_version
Revises: 014_task_conversation_id
"""
from alembic import op
import sqlalchemy as sa


revision = "015_role_version"
down_revision = "014_task_conversation_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("security_roles")}
    if "version" not in cols:
        op.add_column(
            "security_roles",
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )


def downgrade() -> None:
    op.drop_column("security_roles", "version")
