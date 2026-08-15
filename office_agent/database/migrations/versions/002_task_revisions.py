"""Add explicit task revision links."""
from alembic import op
import sqlalchemy as sa

revision = "002_task_revisions"
down_revision = "001_initial"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("task", sa.Column("parent_task_id", sa.String(32), nullable=True))
    op.add_column("task", sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_task_parent_task_id", "task", ["parent_task_id"])
    op.create_foreign_key("fk_task_parent_task_id", "task", "task", ["parent_task_id"], ["id"])

def downgrade() -> None:
    op.drop_constraint("fk_task_parent_task_id", "task", type_="foreignkey")
    op.drop_index("ix_task_parent_task_id", table_name="task")
    op.drop_column("task", "revision_number")
    op.drop_column("task", "parent_task_id")
