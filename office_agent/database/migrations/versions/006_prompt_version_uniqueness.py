"""Make prompt name/version an upsert key.

Revision ID: 006_prompt_version_uniqueness
Revises: 005_file_version_uniqueness
"""
from alembic import op
import sqlalchemy as sa

revision = "006_prompt_version_uniqueness"
down_revision = "005_file_version_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {
        item.get("name")
        for item in sa.inspect(op.get_bind()).get_unique_constraints("prompt_config")
    }
    if "uq_prompt_config_name_version" in existing:
        return
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, name, version FROM prompt_config ORDER BY created_at, id"
    )).mappings()
    seen = set()
    for row in rows:
        key = (row["name"], row["version"])
        if key not in seen:
            seen.add(key)
            continue
        # 历史重复记录保留，但赋予稳定的迁移版本名后缀。
        suffix = f"-dup-{str(row['id'])[-4:]}"
        candidate = f"{str(row['version'])[:16 - len(suffix)]}{suffix}"
        counter = 1
        while (row["name"], candidate) in seen:
            suffix = f"-d{counter}-{str(row['id'])[-3:]}"
            candidate = f"{str(row['version'])[:16 - len(suffix)]}{suffix}"
            counter += 1
        bind.execute(
            sa.text("UPDATE prompt_config SET version = :version WHERE id = :id"),
            {"version": candidate, "id": row["id"]},
        )
        seen.add((row["name"], candidate))
    with op.batch_alter_table("prompt_config") as batch_op:
        batch_op.create_unique_constraint(
            "uq_prompt_config_name_version", ["name", "version"]
        )


def downgrade() -> None:
    existing = {
        item.get("name")
        for item in sa.inspect(op.get_bind()).get_unique_constraints("prompt_config")
    }
    if "uq_prompt_config_name_version" not in existing:
        return
    with op.batch_alter_table("prompt_config") as batch_op:
        batch_op.drop_constraint(
            "uq_prompt_config_name_version", type_="unique"
        )
