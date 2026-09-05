"""文件 Repository"""
import json
from typing import Optional, List
from datetime import datetime, timedelta
from sqlalchemy import select, and_
from sqlalchemy.orm import Session
from ..time import utc_now

from .base import BaseRepository
from ..models.file import File, FileVersion


class FileRepository(BaseRepository[File]):
    def __init__(self, session: Session):
        super().__init__(session, File)

    def get_by_hash(self, file_hash: str) -> Optional[File]:
        return self.find_one(file_hash=file_hash, status="ready")

    def get_by_owner(self, owner_id: str, offset: int = 0, limit: int = 100) -> List[File]:
        offset, limit = self._page(offset, limit)
        stmt = select(File).where(and_(
            File.owner_id == owner_id,
            File.status.notin_(("deleted", "deleting")),
        )).offset(offset).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def get_by_type(self, file_type: str, offset: int = 0, limit: int = 100) -> List[File]:
        offset, limit = self._page(offset, limit)
        stmt = select(File).where(and_(
            File.file_type == file_type,
            File.status.notin_(("deleted", "deleting")),
        )).offset(offset).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def create_file(self, original_name: str, file_type: str,
                    extension: str, storage_path: str, file_size: int,
                    bucket: str = "uploads", storage_backend: str = "local",
                    owner_id: str = None, file_hash: str = None,
                    mime_type: str = None, metadata: dict = None,
                    version: int = 1, parent_file_id: str = None,
                    change_description: str = None,
                    expires_at: datetime = None,
                    file_id: str = None) -> File:
        # 只写权威字段：original_name / storage_path。
        # legacy 兼容列 filename / file_path 由 models/file.py 的
        # before_insert delegate 自动派生，任何调用方不得独立写入。
        f = File(
            id=file_id,
            original_name=original_name,
            file_type=file_type,
            extension=extension,
            storage_path=storage_path,
            bucket=bucket,
            storage_backend=storage_backend,
            file_size=file_size,
            owner_id=owner_id,
            file_hash=file_hash,
            mime_type=mime_type,
            metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
            version=version,
            parent_file_id=parent_file_id,
            change_description=change_description,
            expires_at=expires_at,
            status="ready",
        )
        return self.create(f)

    def update_status(self, file_id: str, status: str):
        self.update(file_id, {"status": status})

    def mark_deleted(self, file_id: str):
        self.update(file_id, {"status": "deleted", "deleted_at": utc_now()})

    def restore_deleted(self, file_id: str):
        self.update(file_id, {"status": "ready", "deleted_at": None})

    def mark_archived(self, file_id: str):
        self.update(file_id, {"status": "archived"})

    def record_access(self, file_id: str):
        """记录访问"""
        self.update(file_id, {
            "access_count": File.access_count + 1,
            "last_accessed_at": utc_now(),
        })

    def get_expired_temp_files(self, hours: int = 24) -> List[File]:
        """获取过期的临时文件"""
        cutoff = utc_now() - timedelta(hours=hours)
        stmt = select(File).where(and_(
            File.bucket == "temp",
            File.status.notin_(("deleted", "deleting")),
            File.created_at < cutoff,
        ))
        return list(self.session.execute(stmt).scalars().all())

    def get_old_versions(self, keep: int = 5) -> List[FileVersion]:
        """获取需要归档的旧版本（每个文件保留最近 N 个版本）"""
        from sqlalchemy import func

        try:
            keep = max(0, int(keep))
        except (TypeError, ValueError):
            keep = 5

        ranked = select(
            FileVersion.id.label("version_id"),
            func.row_number().over(
                partition_by=FileVersion.parent_file_id,
                order_by=FileVersion.version_number.desc(),
            ).label("version_rank"),
        ).subquery()
        stmt = (
            select(FileVersion)
            .join(ranked, ranked.c.version_id == FileVersion.id)
            .where(ranked.c.version_rank > keep)
            .order_by(FileVersion.parent_file_id, FileVersion.version_number.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_storage_stats(self) -> dict:
        """存储统计"""
        from sqlalchemy import func
        total = self.session.execute(
            select(func.count(File.id)).where(File.status.notin_(("deleted", "deleting")))
        ).scalar() or 0
        total_size = self.session.execute(
            select(func.coalesce(func.sum(File.file_size), 0)).where(
                File.status.notin_(("deleted", "deleting"))
            )
        ).scalar() or 0
        by_type = {}
        for t in ["word", "ppt", "excel", "pdf", "text", "image"]:
            count = self.session.execute(
                select(func.count(File.id)).where(
                    and_(File.file_type == t,
                         File.status.notin_(("deleted", "deleting")))
                )
            ).scalar() or 0
            if count:
                by_type[t] = count
        return {
            "total_files": total,
            "total_size_bytes": total_size,
            "by_type": by_type,
        }


class FileVersionRepository(BaseRepository[FileVersion]):
    def __init__(self, session: Session):
        super().__init__(session, FileVersion)

    def get_by_file(self, file_id: str) -> List[FileVersion]:
        stmt = select(FileVersion).where(
            FileVersion.parent_file_id == file_id
        ).order_by(FileVersion.version_number.asc())
        return list(self.session.execute(stmt).scalars().all())

    def get_latest_version(self, file_id: str) -> Optional[FileVersion]:
        stmt = select(FileVersion).where(
            FileVersion.parent_file_id == file_id
        ).order_by(FileVersion.version_number.desc()).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_next_version_number(self, file_id: str) -> int:
        # PostgreSQL/MySQL 上锁住父文件行，使“取号+创建版本”可在同一事务内
        # 串行化；SQLite 由唯一约束作为最终并发保护。
        self.session.execute(
            select(File.id).where(File.id == file_id).with_for_update()
        )
        latest = self.get_latest_version(file_id)
        return (latest.version_number + 1) if latest else 1

    def create_version(self, parent_file_id: str, version_number: int,
                       storage_path: str, file_size: int,
                       file_hash: str = None, change_description: str = None,
                       changed_by: str = None, metadata: dict = None) -> FileVersion:
        v = FileVersion(
            parent_file_id=parent_file_id,
            version_number=version_number,
            storage_path=storage_path,
            file_size=file_size,
            file_hash=file_hash,
            change_description=change_description,
            changed_by=changed_by,
            metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
        )
        return self.create(v)
