"""initial schema

Revision ID: 001_initial
Revises:
Create Date: 2026-07-31
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # user
    op.create_table(
        "user",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("email", sa.String(128), unique=True, index=True),
        sa.Column("password_hash", sa.String(256)),
        sa.Column("display_name", sa.String(64)),
        sa.Column("role", sa.String(32), server_default="user"),
        sa.Column("is_active", sa.Boolean, server_default="1"),
        sa.Column("avatar", sa.String(256)),
        sa.Column("last_login", sa.DateTime(timezone=True)),
        sa.Column("preferences", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # file
    op.create_table(
        "file",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("filename", sa.String(256), nullable=False),
        sa.Column("original_name", sa.String(256), nullable=False),
        sa.Column("file_type", sa.String(32), nullable=False, index=True),
        sa.Column("extension", sa.String(16), nullable=False),
        sa.Column("file_path", sa.String(512), nullable=False),
        sa.Column("file_size", sa.BigInteger, server_default="0"),
        sa.Column("file_hash", sa.String(64), index=True),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("user.id"), index=True),
        sa.Column("metadata_json", sa.Text),
        sa.Column("status", sa.String(32), server_default="uploaded"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # task
    op.create_table(
        "task",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("task_type", sa.String(64), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending", index=True),
        sa.Column("agent_name", sa.String(64), index=True),
        sa.Column("instruction", sa.Text, nullable=False),
        sa.Column("progress", sa.Integer, server_default="0"),
        sa.Column("current_step", sa.String(256)),
        sa.Column("input_file_ids", sa.Text),
        sa.Column("output_file_ids", sa.Text),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("user.id"), index=True),
        sa.Column("result_json", sa.Text),
        sa.Column("error_message", sa.Text),
        sa.Column("options_json", sa.Text),
        sa.Column("priority", sa.Integer, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("quality_score", sa.Float),
        sa.Column("feedback_rating", sa.Integer),
        sa.Column("feedback_comment", sa.Text),
        sa.Column("callback_url", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # agent_config
    op.create_table(
        "agent_config",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("agent_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("agent_type", sa.String(32), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("version", sa.String(32), server_default="1.0.0"),
        sa.Column("capabilities", sa.Text),
        sa.Column("model_config", sa.Text),
        sa.Column("prompt_template", sa.Text),
        sa.Column("config_json", sa.Text),
        sa.Column("enabled", sa.Boolean, server_default="1"),
        sa.Column("status", sa.String(32), server_default="available"),
        sa.Column("priority", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # skill
    op.create_table(
        "skill",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, index=True),
        sa.Column("skill_type", sa.String(64)),
        sa.Column("description", sa.Text),
        sa.Column("prompt", sa.Text),
        sa.Column("version", sa.String(32), server_default="1.0.0"),
        sa.Column("category", sa.String(64)),
        sa.Column("tags", sa.Text),
        sa.Column("config_json", sa.Text),
        sa.Column("enabled", sa.Boolean, server_default="1"),
        sa.Column("usage_count", sa.Integer, server_default="0"),
        sa.Column("rating", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # template
    op.create_table(
        "template",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, index=True),
        sa.Column("template_type", sa.String(32), nullable=False),
        sa.Column("file_path", sa.String(512)),
        sa.Column("file_id", sa.String(32)),
        sa.Column("config_json", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("category", sa.String(64)),
        sa.Column("tags", sa.Text),
        sa.Column("preview_image", sa.String(512)),
        sa.Column("usage_count", sa.Integer, server_default="0"),
        sa.Column("is_builtin", sa.Boolean, server_default="0"),
        sa.Column("version", sa.String(32), server_default="1.0.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # knowledge
    op.create_table(
        "knowledge",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False, index=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", sa.Text),
        sa.Column("embedding_model", sa.String(64)),
        sa.Column("source", sa.String(256)),
        sa.Column("source_type", sa.String(32)),
        sa.Column("category", sa.String(64), index=True),
        sa.Column("tags", sa.Text),
        sa.Column("metadata_json", sa.Text),
        sa.Column("chunk_index", sa.Integer, server_default="0"),
        sa.Column("total_chunks", sa.Integer, server_default="1"),
        sa.Column("relevance_score", sa.Float, server_default="0.0"),
        sa.Column("usage_count", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # execution_log
    op.create_table(
        "execution_log",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("task_id", sa.String(32), sa.ForeignKey("task.id"), index=True),
        sa.Column("agent", sa.String(64), nullable=False, index=True),
        sa.Column("model", sa.String(64)),
        sa.Column("action", sa.String(128)),
        sa.Column("input_text", sa.Text),
        sa.Column("output_text", sa.Text),
        sa.Column("prompt_tokens", sa.Integer, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, server_default="0"),
        sa.Column("total_tokens", sa.Integer, server_default="0"),
        sa.Column("cost", sa.Float, server_default="0.0"),
        sa.Column("execution_time_ms", sa.Integer, server_default="0"),
        sa.Column("status", sa.String(32), server_default="success"),
        sa.Column("error_message", sa.Text),
        sa.Column("metadata_json", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # memory
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
    op.drop_table("memory")
    op.drop_table("execution_log")
    op.drop_table("knowledge")
    op.drop_table("template")
    op.drop_table("skill")
    op.drop_table("agent_config")
    op.drop_table("task")
    op.drop_table("file")
    op.drop_table("user")
