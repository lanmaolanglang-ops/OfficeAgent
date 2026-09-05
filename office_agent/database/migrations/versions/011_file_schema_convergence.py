"""Converge the file table on a single authoritative identity/path contract.

Revision ID: 011_file_schema_convergence
Revises: 010_config_json_columns

权威契约（authoritative contract）：

- ``original_name`` 是用户可见原始文件名的唯一权威字段；``filename`` 是
  001 时代遗留的兼容列，只允许从权威列 delegate 派生。
- ``storage_path`` 是持久化存储相对路径的唯一权威字段；``file_path`` 是
  001 时代遗留的兼容列（004 起 nullable），只允许 delegate 派生。

真实历史 writer 证据：FileRepository.create_file 始终
``filename = original_name``、``file_path = storage_path`` 双写同值；
004 迁移已把 ``storage_path`` 从 ``file_path`` backfill 并转为权威列；
所有生产 reader（API 响应、下载 Content-Disposition、任务输入输出、
存储路径解析）均使用权威列。因此两列冲突在已知 writer 下不可能发生。

数据策略：

- 兼容列缺失（NULL/空串）而权威列有值 → 从权威列回填（delegate，非新值）。
- 权威列缺失而兼容列有值 → 反向回填（恢复性，仅限手工/外部写入的历史行）。
- 两列均缺失 → fail loudly：该行既无名称/无路径，无法安全恢复。
- 两列冲突 → fail loudly，绝不静默任选：名称冲突改变用户可见文件名，
  路径冲突意味着文件错关联（可能 serve 错误物理文件）。报错携带
  file id 与两列实际值，要求人工核对后重跑。

本迁移只做 schema/reference 收敛，不做任何物理文件移动，不改写路径
分隔符或做 path normalization（运行时唯一 canonical 解析仍是
LocalStorage._full_path），不产生 DB 已指向新路径而物理文件未移动的
崩溃不一致窗口。

downgrade 为 no-op（有意设计）：本迁移不删列、不改类型、只做收敛性
回填；回填后的值在旧 schema 下同样合法（旧代码读旧列得到的正是权威值），
因此 upgrade → downgrade → upgrade 安全且无任何数据丢失。
"""
from alembic import op
import sqlalchemy as sa

revision = "011_file_schema_convergence"
down_revision = "010_config_json_columns"
branch_labels = None
depends_on = None


def _missing(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, filename, original_name, storage_path, file_path FROM file"
    )).mappings().all()

    problems: list[str] = []
    backfills: list[dict] = []
    for row in rows:
        fid = row["id"]
        filename = row["filename"]
        original = row["original_name"]
        storage = row["storage_path"]
        legacy_path = row["file_path"]

        # ---- 名称对：original_name 权威，filename delegate ----
        if _missing(original) and _missing(filename):
            problems.append(
                f"file id={fid}: filename 与 original_name 均为空，"
                "无法确定用户可见文件名，请人工修复后重跑"
            )
        elif _missing(original):
            original = filename  # 恢复性回填：legacy 列是唯一幸存的用户名
        elif _missing(filename):
            filename = original  # delegate 回填
        elif filename != original:
            problems.append(
                f"file id={fid}: filename 与 original_name 冲突 "
                f"(filename={filename!r}, original_name={original!r})；"
                "真实 writer 始终双写同值，冲突即未知来源异常，"
                "拒绝静默任选，请人工核对后重跑"
            )

        # ---- 路径对：storage_path 权威，file_path delegate ----
        if _missing(storage) and _missing(legacy_path):
            problems.append(
                f"file id={fid}: storage_path 与 file_path 均为空，"
                "该行不指向任何物理文件，请人工修复后重跑"
            )
        elif _missing(storage):
            storage = legacy_path  # 恢复性回填：001 时代唯一路径列
        elif _missing(legacy_path):
            legacy_path = storage  # delegate 回填
        elif storage != legacy_path:
            problems.append(
                f"file id={fid}: storage_path 与 file_path 冲突 "
                f"(storage_path={storage!r}, file_path={legacy_path!r})；"
                "路径冲突意味着文件错关联风险，拒绝静默任选，请人工核对后重跑"
            )

        if (filename, original, storage, legacy_path) != (
                row["filename"], row["original_name"],
                row["storage_path"], row["file_path"]):
            backfills.append({
                "fid": fid, "filename": filename, "original": original,
                "storage": storage, "legacy_path": legacy_path,
            })

    if problems:
        raise RuntimeError(
            "file schema convergence aborted — "
            "conflicting/unrecoverable rows detected:\n" + "\n".join(problems)
        )

    for item in backfills:
        bind.execute(sa.text(
            "UPDATE file SET filename=:filename, original_name=:original,"
            " storage_path=:storage, file_path=:legacy_path WHERE id=:fid"
        ), item)


def downgrade() -> None:
    # 有意 no-op：upgrade 只做收敛性回填（兼容列从权威列派生），
    # 不删列、不改类型；回填值在旧 schema 下同样合法且语义一致，
    # 旧代码读旧列得到的正是权威值，因此无需也无法“撤销”。
    pass
