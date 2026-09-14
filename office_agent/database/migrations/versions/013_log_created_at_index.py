"""Index created_at on high-volume log tables for time-ordered queries.

Revision ID: 013_log_created_at_index
Revises: 012_runtime_schema_contract
"""
from alembic import op


revision = "013_log_created_at_index"
down_revision = "012_runtime_schema_contract"
branch_labels = None
depends_on = None


_INDEXES = (
    ("ix_execution_log_created_at", "execution_log"),
    ("ix_model_call_log_created_at", "model_call_log"),
    ("ix_error_log_created_at", "error_log"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for name, table in _INDEXES:
        existing = {
            ix["name"]
            for ix in __import__("sqlalchemy").inspect(bind).get_indexes(table)
        }
        if name not in existing:
            op.create_index(name, table, ["created_at"])


def downgrade() -> None:
    for name, table in _INDEXES:
        op.drop_index(name, table_name=table)
