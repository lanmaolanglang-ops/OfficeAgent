"""Migrate config ORM JSON fields from Text to SQLAlchemy JSON.

Revision ID: 010_config_json_columns
Revises: 009_normalize_task_status
"""
import json

from alembic import op
import sqlalchemy as sa


revision = "010_config_json_columns"
down_revision = "009_normalize_task_status"
branch_labels = None
depends_on = None


#: table -> [(column, expected JSON shape)].
#: 只迁移 config.py 中语义明确为配置结构的 Text JSON 字段。
_JSON_FIELDS = {
    "model_config": [
        ("tags", "list"),
        ("config_json", "dict"),
    ],
    "agent_runtime_config": [
        ("model_priority", "list"),
        ("fallback_models", "list"),
        ("available_tools", "list"),
        ("retry_config", "dict"),
        ("tags", "list"),
        ("config_json", "dict"),
    ],
    "prompt_config": [
        ("variables", "list"),
        ("tags", "list"),
        ("config_json", "dict"),
    ],
    "skill_config": [
        ("workflow", "list"),
        ("tools", "list"),
        ("trigger_keywords", "list"),
        ("trigger_patterns", "list"),
        ("parameters", "dict"),
        ("tags", "list"),
        ("config_json", "dict"),
    ],
    "workflow_config": [
        ("steps", "list"),
        ("input_schema", "dict"),
        ("output_schema", "dict"),
        ("tags", "list"),
        ("config_json", "dict"),
    ],
    "file_rule_config": [
        ("actions", "list"),
        ("parameters", "dict"),
        ("config_json", "dict"),
    ],
}


def _default_value(kind: str):
    return [] if kind == "list" else {}


def _normalize_legacy_json(raw, table: str, row_id: str, column: str,
                           kind: str):
    if raw is None:
        return _default_value(kind)
    if isinstance(raw, (list, dict)):
        return raw

    text = str(raw).strip()
    if not text:
        return _default_value(kind)

    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"无法安全迁移配置 JSON: table={table} id={row_id} "
            f"column={column} value={text!r}"
        ) from exc

    if parsed is None:
        return _default_value(kind)

    # 兼容历史 double-encoded JSON string。
    if isinstance(parsed, str):
        inner = parsed.strip()
        try:
            parsed = json.loads(inner)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None

    expected = list if kind == "list" else dict
    if not isinstance(parsed, expected):
        raise RuntimeError(
            f"配置 JSON 形状不符合字段语义: table={table} id={row_id} "
            f"column={column} expected={kind} value={text!r}"
        )
    return parsed


def _convert_rows_to_json(bind) -> None:
    inspector = sa.inspect(bind)
    for table, fields in _JSON_FIELDS.items():
        if table not in inspector.get_table_names():
            continue
        for column, kind in fields:
            rows = bind.execute(
                sa.text(f'SELECT id, "{column}" FROM "{table}"')
            ).mappings().all()
            for row in rows:
                normalized = _normalize_legacy_json(
                    row[column], table, str(row["id"]), column, kind,
                )
                bind.execute(
                    sa.text(f'UPDATE "{table}" SET "{column}" = :value WHERE id = :id'),
                    {
                        "value": json.dumps(normalized, ensure_ascii=False),
                        "id": row["id"],
                    },
                )


def upgrade() -> None:
    _convert_rows_to_json(op.get_bind())
    inspector = sa.inspect(op.get_bind())
    for table, fields in _JSON_FIELDS.items():
        if table not in inspector.get_table_names():
            continue
        with op.batch_alter_table(table) as batch_op:
            for column, _ in fields:
                batch_op.alter_column(
                    column,
                    existing_type=sa.Text(),
                    type_=sa.JSON(),
                    existing_nullable=False,
                )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, fields in _JSON_FIELDS.items():
        if table not in inspector.get_table_names():
            continue
        with op.batch_alter_table(table) as batch_op:
            for column, _ in fields:
                batch_op.alter_column(
                    column,
                    existing_type=sa.JSON(),
                    type_=sa.Text(),
                    existing_nullable=False,
                )
