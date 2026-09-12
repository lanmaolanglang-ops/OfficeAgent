"""Add explicit task revision links."""
from alembic import op
import sqlalchemy as sa

revision = "002_task_revisions"
down_revision = "001_initial"
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("task")}
    indexes = {item["name"] for item in inspector.get_indexes("task")}
    foreign_keys = inspector.get_foreign_keys("task")

    # SQLite 不支持直接 ALTER TABLE ADD CONSTRAINT，必须使用 batch 模式。
    with op.batch_alter_table("task") as batch_op:
        if "parent_task_id" not in columns:
            batch_op.add_column(sa.Column("parent_task_id", sa.String(32), nullable=True))
        if "revision_number" not in columns:
            batch_op.add_column(sa.Column(
                "revision_number", sa.Integer(), nullable=False, server_default="1"
            ))
        if "ix_task_parent_task_id" not in indexes:
            batch_op.create_index("ix_task_parent_task_id", ["parent_task_id"])
        if not any(
            "parent_task_id" in item.get("constrained_columns", [])
            for item in foreign_keys
        ):
            # 与 ORM 契约一致（models/task.py: ondelete="SET NULL"）：
            # 删除父任务时子任务的 parent_task_id 置空而不是阻止删除。
            batch_op.create_foreign_key(
                "fk_task_parent_task_id", "task", ["parent_task_id"], ["id"],
                ondelete="SET NULL",
            )

def downgrade() -> None:
    with op.batch_alter_table("task") as batch_op:
        batch_op.drop_constraint("fk_task_parent_task_id", type_="foreignkey")
        batch_op.drop_index("ix_task_parent_task_id")
        batch_op.drop_column("revision_number")
        batch_op.drop_column("parent_task_id")
