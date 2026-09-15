"""Add deterministic user-instruction fields to the existing skill table.

Revision ID: 017_agent_skills
Revises: 016_task_revision_unique
"""
from alembic import op
import sqlalchemy as sa

revision = "017_agent_skills"
down_revision = "016_task_revision_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("skill")}
    with op.batch_alter_table("skill") as batch_op:
        if "target_agents" not in columns:
            batch_op.add_column(sa.Column(
                "target_agents", sa.Text(), nullable=False, server_default='["all"]'
            ))
        if "priority" not in columns:
            batch_op.add_column(sa.Column("priority", sa.Integer(), nullable=False, server_default="100"))
        if "source" not in columns:
            batch_op.add_column(sa.Column("source", sa.String(32), nullable=False, server_default="ui"))
        if "owner_id" not in columns:
            batch_op.add_column(sa.Column("owner_id", sa.String(64), nullable=True))
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("skill")}
    with op.batch_alter_table("skill") as batch_op:
        if "ix_skill_priority" not in indexes:
            batch_op.create_index("ix_skill_priority", ["priority"], unique=False)
        if "ix_skill_owner_id" not in indexes:
            batch_op.create_index("ix_skill_owner_id", ["owner_id"], unique=False)


def downgrade() -> None:
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("skill")}
    with op.batch_alter_table("skill") as batch_op:
        if "ix_skill_owner_id" in indexes:
            batch_op.drop_index("ix_skill_owner_id")
        if "ix_skill_priority" in indexes:
            batch_op.drop_index("ix_skill_priority")
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("skill")}
    with op.batch_alter_table("skill") as batch_op:
        for name in ("owner_id", "source", "priority", "target_agents"):
            if name in columns:
                batch_op.drop_column(name)
