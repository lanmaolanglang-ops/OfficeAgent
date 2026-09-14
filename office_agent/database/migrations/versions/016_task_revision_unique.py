"""Prevent duplicate revision numbers for one parent task.

Revision ID: 016_task_revision_unique
Revises: 015_role_version
"""
from alembic import op
import sqlalchemy as sa

revision = "016_task_revision_unique"
down_revision = "015_role_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {
        item.get("name")
        for item in sa.inspect(op.get_bind()).get_unique_constraints("task")
    }
    if "uq_task_parent_revision" in existing:
        return
    with op.batch_alter_table("task") as batch_op:
        batch_op.create_unique_constraint(
            "uq_task_parent_revision",
            ["parent_task_id", "revision_number"],
        )


def downgrade() -> None:
    existing = {
        item.get("name")
        for item in sa.inspect(op.get_bind()).get_unique_constraints("task")
    }
    if "uq_task_parent_revision" not in existing:
        return
    with op.batch_alter_table("task") as batch_op:
        batch_op.drop_constraint("uq_task_parent_revision", type_="unique")
