"""Prevent duplicate version numbers for one file.

Revision ID: 005_file_version_uniqueness
Revises: 004_reconcile_runtime_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "005_file_version_uniqueness"
down_revision = "004_reconcile_runtime_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {
        item.get("name")
        for item in sa.inspect(op.get_bind()).get_unique_constraints("file_version")
    }
    if "uq_file_version_parent_number" in existing:
        return
    with op.batch_alter_table("file_version") as batch_op:
        batch_op.create_unique_constraint(
            "uq_file_version_parent_number",
            ["parent_file_id", "version_number"],
        )


def downgrade() -> None:
    existing = {
        item.get("name")
        for item in sa.inspect(op.get_bind()).get_unique_constraints("file_version")
    }
    if "uq_file_version_parent_number" not in existing:
        return
    with op.batch_alter_table("file_version") as batch_op:
        batch_op.drop_constraint(
            "uq_file_version_parent_number", type_="unique"
        )
