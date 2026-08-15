"""文件模型"""
import uuid
from sqlalchemy import String, Integer, BigInteger, ForeignKey, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

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

    # 存储信息
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    # 兼容旧字段 file_path
    file_path: Mapped[str] = mapped_column(String(512), nullable=True)
    bucket: Mapped[str] = mapped_column(String(32), default="uploads")
    storage_backend: Mapped[str] = mapped_column(String(32), default="local")

    # 大小和哈希
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=True, index=True)

    # 所属用户
    owner_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("user.id"), nullable=True, index=True
    )

    # 版本管理
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_file_id: Mapped[str] = mapped_column(String(32), nullable=True, index=True)
    change_description: Mapped[str] = mapped_column(Text, nullable=True)

    # 元数据 JSON
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)

    # 状态：uploading/ready/processing/archived/deleted
    status: Mapped[str] = mapped_column(String(32), default="ready", index=True)

    # 访问控制
    is_public: Mapped[bool] = mapped_column(default=False)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[DateTime] = mapped_column(DateTime, nullable=True)

    # 过期时间（临时文件用）
    expires_at: Mapped[DateTime] = mapped_column(DateTime, nullable=True)

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
        String(32), ForeignKey("file.id"), nullable=False, index=True
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
                               foreign_keys=[parent_file_id])

    def __repr__(self):
        return f"<FileVersion {self.parent_file_id} v{self.version_number}>"
