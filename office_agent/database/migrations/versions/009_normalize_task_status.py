"""Normalize the legacy waiting task status to queued.

Revision ID: 009_normalize_task_status
Revises: 008_seed_database_rbac
"""
from alembic import op
import sqlalchemy as sa

revision = "009_normalize_task_status"
down_revision = "008_seed_database_rbac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE task SET status = 'queued' WHERE status = 'waiting'"
    ))


def downgrade() -> None:
    # Data normalization is intentionally not reversed: queued was already a
    # valid status before this migration, so converting every queued row back
    # would corrupt tasks that were never legacy waiting rows.
    pass
