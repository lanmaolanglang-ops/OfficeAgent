"""Complete the runtime-managed schema contract.

Revision ID: 012_runtime_schema_contract
Revises: 011_file_schema_convergence
"""
from alembic import op
import sqlalchemy as sa


revision = "012_runtime_schema_contract"
down_revision = "011_file_schema_convergence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``memory`` existed in the original Alembic baseline but was never mapped
    # into Base.metadata. Historical desktop databases created via create_all
    # therefore legitimately lack it even though every runtime ORM table is
    # present. Restore the declared Alembic schema when adopting those DBs.
    if "memory" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "memory",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("user.id"), index=True),
        sa.Column("memory_type", sa.String(32), nullable=False, index=True),
        sa.Column("key", sa.String(128), index=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("importance", sa.Float, server_default="0.5"),
        sa.Column("category", sa.String(64)),
        sa.Column("tags", sa.Text),
        sa.Column("source", sa.String(64)),
        sa.Column("metadata_json", sa.Text),
        sa.Column("access_count", sa.Integer, server_default="0"),
        sa.Column("is_active", sa.Boolean, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    # 001 declares this table as baseline schema. Downgrading the repair
    # revision must not remove a table that 011 still expects to exist.
    pass
