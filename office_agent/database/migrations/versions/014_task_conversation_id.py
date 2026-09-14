"""Add structured conversation_id on task for follow-up lookup.

Revision ID: 014_task_conversation_id
Revises: 013_log_created_at_index
"""
from alembic import op
import sqlalchemy as sa


revision = "014_task_conversation_id"
down_revision = "013_log_created_at_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("task")}
    if "conversation_id" not in cols:
        op.add_column("task", sa.Column("conversation_id", sa.String(64), nullable=True))
    op.create_index(
        "ix_task_conversation_id", "task", ["conversation_id"], if_not_exists=True
    )


def downgrade() -> None:
    op.drop_index("ix_task_conversation_id", table_name="task")
    op.drop_column("task", "conversation_id")
