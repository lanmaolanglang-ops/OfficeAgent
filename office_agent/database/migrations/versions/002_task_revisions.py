"""Add explicit task revision links."""
from alembic import op
import sqlalchemy as sa

revision = "002_task_revisions"
down_revision = "001_initial"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # SQLite 不支持直接 ALTER TABLE ADD CONSTRAINT，必须使用 batch 模式。
    with op.batch_alter_table("task") as batch_op:
        batch_op.add_column(sa.Column("parent_task_id", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"))
        batch_op.create_index("ix_task_parent_task_id", ["parent_task_id"])
        batch_op.create_foreign_key(
            "fk_task_parent_task_id", "task", ["parent_task_id"], ["id"]
        )

def downgrade() -> None:
    with op.batch_alter_table("task") as batch_op:
        batch_op.drop_constraint("fk_task_parent_task_id", type_="foreignkey")
        batch_op.drop_index("ix_task_parent_task_id")
        batch_op.drop_column("revision_number")
        batch_op.drop_column("parent_task_id")
