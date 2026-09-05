"""文件模型

Authoritative file identity/path contract（唯一权威契约）：

- ``original_name`` 是用户可见原始文件名的**唯一权威字段**。
  ``filename`` 是 001 时代遗留的兼容列，只保留给旧客户端/旧数据读取，
  其值永远由 ``original_name`` delegate 派生，禁止独立写入。
- ``storage_path`` 是持久化存储相对路径的**唯一权威字段**。
  ``file_path`` 是 001 时代遗留的兼容列（004 起 nullable），
  其值永远由 ``storage_path`` delegate 派生，禁止独立写入。

delegate 由本模块底部的 before_insert/before_update 事件监听统一执行，
任何写入路径（Repository、StorageService、直接 ORM）都无法造成两列漂移。
上传文件与生成的 artifact 共用本契约（仅 bucket 不同）。
"""
import uuid
from datetime import datetime
from sqlalchemy import String, Integer, BigInteger, ForeignKey, Text, DateTime, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TimestampMixin


def _uuid():
    return f"file_{uuid.uuid4().hex[:12]}"


def _version_uuid():
    return f"ver_{uuid.uuid4().hex[:12]}"


class File(Base, TimestampMixin):
    """
    文件表

    状态流转：
        uploading → ready → processing → ready
                         ↘ archived
                         ↘ deleted
    """
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)

    # 文件名
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    original_name: Mapped[str] = mapped_column(String(256), nullable=False)

    # 文件类型
    file_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # word/ppt/excel/pdf/text/image
    extension: Mapped[str] = mapped_column(String(16), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=True)

    # 存储信息：storage_path 为唯一权威字段；file_path 为 delegate-only 兼容列
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    # 兼容旧字段 file_path（deprecated，见模块 docstring 的权威契约）
    file_path: Mapped[str] = mapped_column(String(512), nullable=True)
    bucket: Mapped[str] = mapped_column(String(32), default="uploads")
    storage_backend: Mapped[str] = mapped_column(String(32), default="local")

    # 大小和哈希
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=True, index=True)

    # 所属用户
    owner_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # 版本管理
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_file_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("file.id", ondelete="SET NULL"), nullable=True, index=True
    )
    change_description: Mapped[str] = mapped_column(Text, nullable=True)

    # 元数据 JSON
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)

    # 状态：uploading/ready/processing/archived/deleted
    status: Mapped[str] = mapped_column(String(32), default="ready", index=True)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # 访问控制
    is_public: Mapped[bool] = mapped_column(default=False)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    # 过期时间（临时文件用）
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    # 关系
    owner = relationship("User", back_populates="files")
    versions = relationship("FileVersion", back_populates="parent_file",
                            foreign_keys="FileVersion.parent_file_id",
                            order_by="FileVersion.version_number")

    def __repr__(self):
        return f"<File {self.original_name} v{self.version} ({self.file_type})>"


class FileVersion(Base, TimestampMixin):
    """
    文件版本表

    每次文件修改创建新版本，旧版本保留。
    """
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_version_uuid)

    # 关联主文件
    parent_file_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("file.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 版本号
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # 该版本的存储信息
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=True)

    # 变更说明
    change_description: Mapped[str] = mapped_column(Text, nullable=True)
    changed_by: Mapped[str] = mapped_column(String(32), nullable=True)
    # user / agent_name

    # 元数据
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)

    # 关系
    parent_file = relationship("File", back_populates="versions",
                               foreign_keys=[parent_file_id], passive_deletes=True)

    __table_args__ = (
        UniqueConstraint("parent_file_id", "version_number", name="uq_file_version_parent_number"),
    )

    def __repr__(self):
        return f"<FileVersion {self.parent_file_id} v{self.version_number}>"


def _delegate_file_compat_columns(target: "File") -> None:
    """兼容列只允许 delegate：从权威字段派生，拒绝任何独立写入值。

    - ``filename`` 永远等于 ``original_name``（用户可见原始文件名权威）
    - ``file_path`` 永远等于 ``storage_path``（持久化存储相对路径权威）
    """
    target.filename = target.original_name
    target.file_path = target.storage_path


@event.listens_for(File, "before_insert")
def _file_before_insert(mapper, connection, target):  # noqa: ANN001, ANN202
    _delegate_file_compat_columns(target)


@event.listens_for(File, "before_update")
def _file_before_update(mapper, connection, target):  # noqa: ANN001, ANN202
    _delegate_file_compat_columns(target)
