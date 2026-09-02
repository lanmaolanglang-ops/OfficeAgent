"""Track the time a file entered the recycle bin."""
from alembic import op
import sqlalchemy as sa

revision = "003_file_deleted_at"
down_revision = "002_task_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("file", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_file_deleted_at", "file", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_file_deleted_at", table_name="file")
    op.drop_column("file", "deleted_at")
