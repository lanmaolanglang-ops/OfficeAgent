"""
File Storage Service - 文件存储核心服务

所有 Agent 和 API 都通过此服务访问文件，禁止直接操作文件系统。

职责：
- 上传/下载/删除文件
- 文件校验
- 文件版本管理
- 文件元数据管理
- 分片上传
- 访问控制
- 生命周期管理
"""
import os
import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional, BinaryIO, Tuple, List, Dict, Iterator

from .storage_backend import StorageBackend
from .local_storage import LocalStorage
from .validators import (
    validate_file, validate_fileobj, validate_extension, validate_size, get_file_category,
    get_mime_type, compute_hash, FileValidationError,
    DEFAULT_MAX_SIZE, CHUNK_SIZE,
)
from .path_generator import (
    generate_file_id, generate_storage_path,
    BUCKET_UPLOADS, BUCKET_OUTPUTS,
)
from ..runtime_config import get_data_root

logger = logging.getLogger("office_agent.storage")

# 永久删除的 tombstone 冷却窗口（秒）。
#
# delete(permanent=True) 分两步：先把记录置为 deleting 并提交，再删物理内容。
# 这两个步骤之间不存在锁，因此 GC 必须避让**正在并发进行**的删除：刚写下的
# tombstone updated_at 就是当下时间，短于本窗口的视为"进行中"，不抢。
# 这里刻意不引入 lease / 后台调度器：updated_at + 冷却窗口已能覆盖单机与
# 多 worker 场景，且不需要新状态。
DELETING_GRACE_SECONDS = 300.0


class StorageConfig:
    """存储配置"""
    def __init__(self, storage_type: str = "local",
                 local_path: str | None = None,
                 minio_endpoint: str | None = None, minio_access_key: str | None = None,
                 minio_secret_key: str | None = None, minio_bucket: str = "office-agent",
                 minio_secure: bool = False,
                 s3_bucket: str | None = None, s3_access_key: str | None = None,
                 s3_secret_key: str | None = None, s3_region: str | None = None,
                 s3_endpoint_url: str | None = None,
                 max_file_size: int = DEFAULT_MAX_SIZE,
                 temp_expire_hours: int = 24,
                 max_versions: int = 10):
        self.storage_type = storage_type
        self.local_path = local_path or str(get_data_root() / "storage")
        self.minio_endpoint = minio_endpoint
        self.minio_access_key = minio_access_key
        self.minio_secret_key = minio_secret_key
        self.minio_bucket = minio_bucket
        self.minio_secure = minio_secure
        self.s3_bucket = s3_bucket
        self.s3_access_key = s3_access_key
        self.s3_secret_key = s3_secret_key
        self.s3_region = s3_region
        self.s3_endpoint_url = s3_endpoint_url
        self.max_file_size = max_file_size
        self.temp_expire_hours = temp_expire_hours
        self.max_versions = max_versions

    @classmethod
    def from_env(cls) -> "StorageConfig":
        """从环境变量加载配置"""
        return cls(
            storage_type=os.environ.get("STORAGE_TYPE", "local"),
            local_path=os.environ.get("LOCAL_STORAGE_PATH"),
            minio_endpoint=os.environ.get("MINIO_ENDPOINT"),
            minio_access_key=os.environ.get("MINIO_ACCESS_KEY"),
            minio_secret_key=os.environ.get("MINIO_SECRET_KEY"),
            minio_bucket=os.environ.get("MINIO_BUCKET", "office-agent"),
            minio_secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
            s3_bucket=os.environ.get("S3_BUCKET"),
            s3_access_key=os.environ.get("AWS_ACCESS_KEY_ID"),
            s3_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            s3_region=os.environ.get("AWS_REGION"),
            s3_endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
            max_file_size=int(os.environ.get("MAX_FILE_SIZE", DEFAULT_MAX_SIZE)),
            temp_expire_hours=int(os.environ.get("TEMP_EXPIRE_HOURS", "24")),
            max_versions=int(os.environ.get("MAX_VERSIONS", "10")),
        )


def create_storage_backend(config: StorageConfig) -> StorageBackend:
    """根据配置创建存储后端"""
    if config.storage_type == "local":
        return LocalStorage(config.local_path)
    else:
        raise ValueError(f"不支持的存储类型: {config.storage_type}（当前仅支持 local）")


class FileInfo:
    """文件信息（业务对象）"""
    def __init__(self, file_id: str, original_name: str, storage_path: str,
                 file_type: str, extension: str, mime_type: str,
                 size: int, file_hash: str | None = None, bucket: str = "uploads",
                 version: int = 1, status: str = "ready",
                 owner_id: str | None = None, parent_file_id: str | None = None,
                 change_description: str | None = None,
                 created_at: datetime | None = None, deleted_at: datetime | None = None,
                 metadata: dict | None = None):
        self.file_id = file_id
        self.original_name = original_name
        self.storage_path = storage_path
        self.file_type = file_type
        self.extension = extension
        self.mime_type = mime_type
        self.size = size
        self.file_hash = file_hash
        self.bucket = bucket
        self.version = version
        self.status = status
        self.owner_id = owner_id
        self.parent_file_id = parent_file_id
        self.change_description = change_description
        self.created_at = created_at or datetime.now(timezone.utc)
        self.deleted_at = deleted_at
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "original_name": self.original_name,
            "file_type": self.file_type,
            "extension": self.extension,
            "mime_type": self.mime_type,
            "size": self.size,
            "hash": self.file_hash,
            "bucket": self.bucket,
            "version": self.version,
            "status": self.status,
            "owner_id": self.owner_id,
            "parent_file_id": self.parent_file_id,
            "change_description": self.change_description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "metadata": self.metadata,
        }


class StorageService:
    """
    文件存储服务

    Agent 和 API 的统一文件访问入口。
    """

    def __init__(self, config: StorageConfig | None = None, backend: StorageBackend | None = None):
        self.config = config or StorageConfig.from_env()
        self.backend = backend or create_storage_backend(self.config)
        self._version_locks: Dict[str, tuple[threading.RLock, int]] = {}
        self._version_locks_guard = threading.Lock()
        logger.info(f"StorageService 初始化: type={self.config.storage_type}")

    @contextmanager
    def _version_lock(self, file_id: str):
        with self._version_locks_guard:
            entry = self._version_locks.get(file_id)
            if entry is None:
                lock = threading.RLock()
                self._version_locks[file_id] = (lock, 1)
            else:
                lock, users = entry
                self._version_locks[file_id] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._version_locks_guard:
                current = self._version_locks.get(file_id)
                if current and current[0] is lock:
                    if current[1] == 1:
                        del self._version_locks[file_id]
                    else:
                        self._version_locks[file_id] = (lock, current[1] - 1)

    def _get_session(self):
        """获取数据库 session"""
        from ..database.session import SessionLocal
        return SessionLocal()

    # ===== 上传 =====

    def upload(self, filename: str, content: bytes,
               owner_id: str | None = None, bucket: str = BUCKET_UPLOADS,
               metadata: dict | None = None) -> FileInfo:
        """
        上传文件

        Args:
            filename: 原始文件名
            content: 文件内容
            owner_id: 上传者ID
            bucket: 存储桶（uploads/outputs/temp/cache）
            metadata: 额外元数据

        Returns:
            FileInfo
        """
        # 1. 校验
        info = validate_file(filename, content, self.config.max_file_size)

        # 2. 生成路径
        file_id = generate_file_id()
        storage_path = generate_storage_path(bucket, file_id, info["extension"])

        # 3. 存储
        self.backend.upload(storage_path, content, info["mime_type"])

        # 4. 写数据库
        session = self._get_session()
        committed = False
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.create_file(
                file_id=file_id,
                original_name=info["original_name"],
                file_type=info["file_type"],
                extension=info["extension"],
                storage_path=storage_path,
                file_size=info["size"],
                bucket=bucket,
                storage_backend=self.config.storage_type,
                owner_id=owner_id,
                file_hash=info["hash"],
                mime_type=info["mime_type"],
                metadata=metadata,
            )
            session.commit()
            committed = True

            return FileInfo(
                file_id=db_file.id,
                original_name=db_file.original_name,
                storage_path=db_file.storage_path,
                file_type=db_file.file_type,
                extension=db_file.extension,
                mime_type=db_file.mime_type,
                size=db_file.file_size,
                file_hash=db_file.file_hash,
                bucket=db_file.bucket,
                version=db_file.version,
                status=db_file.status,
                owner_id=db_file.owner_id,
                created_at=db_file.created_at,
                metadata=json.loads(db_file.metadata_json) if db_file.metadata_json else {},
            )
        except Exception:
            session.rollback()
            if not committed:
                try:
                    self.backend.delete(storage_path)
                except Exception:
                    logger.exception("上传数据库提交失败后清理物理文件失败: %s", storage_path)
            raise
        finally:
            session.close()

    def upload_fileobj(self, filename: str, fileobj: BinaryIO,
                       owner_id: str | None = None, bucket: str = BUCKET_UPLOADS,
                       metadata: dict | None = None) -> FileInfo:
        """从可 seek 文件对象流式校验并上传，避免复制整份内容到内存。"""
        info = validate_fileobj(filename, fileobj, self.config.max_file_size)
        file_id = generate_file_id()
        storage_path = generate_storage_path(bucket, file_id, info["extension"])
        fileobj.seek(0)
        self.backend.upload_fileobj(storage_path, fileobj, info["mime_type"])

        session = self._get_session()
        committed = False
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.create_file(
                file_id=file_id,
                original_name=info["original_name"],
                file_type=info["file_type"],
                extension=info["extension"],
                storage_path=storage_path,
                file_size=info["size"],
                bucket=bucket,
                storage_backend=self.config.storage_type,
                owner_id=owner_id,
                file_hash=info["hash"],
                mime_type=info["mime_type"],
                metadata=metadata,
            )
            session.commit()
            committed = True
            return FileInfo(
                file_id=db_file.id,
                original_name=db_file.original_name,
                storage_path=db_file.storage_path,
                file_type=db_file.file_type,
                extension=db_file.extension,
                mime_type=db_file.mime_type,
                size=db_file.file_size,
                file_hash=db_file.file_hash,
                bucket=db_file.bucket,
                version=db_file.version,
                status=db_file.status,
                owner_id=db_file.owner_id,
                created_at=db_file.created_at,
                metadata=json.loads(db_file.metadata_json) if db_file.metadata_json else {},
            )
        except Exception:
            session.rollback()
            if not committed:
                try:
                    self.backend.delete(storage_path)
                except Exception:
                    logger.exception("流式上传数据库提交失败后清理物理文件失败: %s", storage_path)
            raise
        finally:
            session.close()

    def save_output(self, source_path: str, original_name: str | None = None,
                    owner_id: str | None = None, change_description: str | None = None,
                    parent_file_id: str | None = None) -> FileInfo:
        """
        保存 Agent 生成的输出文件

        Args:
            source_path: 源文件路径（Agent 生成的临时文件）
            original_name: 输出文件名
            owner_id: 所有者
            change_description: 变更说明
            parent_file_id: 父文件ID（版本管理）
        """
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"源文件不存在: {source_path}")

        with open(source_path, "rb") as f:
            content = f.read()

        original_name = original_name or os.path.basename(source_path)

        # 如果有父文件，创建新版本
        if parent_file_id:
            return self.create_version(
                parent_file_id=parent_file_id,
                content=content,
                change_description=change_description or "AI 处理",
                changed_by=owner_id or "agent",
            )

        return self.upload(
            filename=original_name,
            content=content,
            owner_id=owner_id,
            bucket=BUCKET_OUTPUTS,
            metadata={"source": "agent_output", "change_description": change_description},
        )

    def save_new_output(self, source_path: str, original_name: str | None = None,
                        owner_id: str | None = None, change_description: str | None = None) -> FileInfo:
        """
        保存 Agent 生成的独立输出文件（不发生版本覆盖）。

        与 save_output 的区别:
        - 始终写入 outputs 储存桶
        - 创建新的 File 记录和新 file_id
        - 不使用 parent_file_id，不触发版本化逻辑
        - 输入文件记录保持不变
        """
        info = self.save_output(
            source_path=source_path,
            original_name=original_name,
            owner_id=owner_id,
            change_description=change_description,
            parent_file_id=None,
        )
        # Agent 的 outputs/ 文件是登记前的临时副本；Storage 成功持久化后删除，
        # 防止永久删除只清理受管路径却遗留同内容副本。
        try:
            os.remove(source_path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("输出登记成功但临时副本清理失败: %s", source_path)
        return info

# ===== 下载 =====

    def download(self, file_id: str) -> Tuple[bytes, FileInfo]:
        """
        下载文件

        Returns:
            (content, FileInfo)
        """
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.get_by_id(file_id)
            if not db_file or db_file.status in {"deleted", "deleting"}:
                raise FileNotFoundError(f"文件不存在: {file_id}")

            content = self.backend.download(db_file.storage_path)

            # 记录访问
            repo.record_access(file_id)
            session.commit()

            info = FileInfo(
                file_id=db_file.id,
                original_name=db_file.original_name,
                storage_path=db_file.storage_path,
                file_type=db_file.file_type,
                extension=db_file.extension,
                mime_type=db_file.mime_type or "application/octet-stream",
                size=db_file.file_size,
                file_hash=db_file.file_hash,
                bucket=db_file.bucket,
                version=db_file.version,
                status=db_file.status,
                owner_id=db_file.owner_id,
                created_at=db_file.created_at,
            )
            return content, info
        finally:
            session.close()

    def stream_download(self, file_id: str) -> Tuple[Iterator[bytes], FileInfo]:
        """
        流式下载（大文件不整读进内存）

        Returns:
            (chunk 迭代器, FileInfo)
        """
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.get_by_id(file_id)
            if not db_file or db_file.status in {"deleted", "deleting"}:
                raise FileNotFoundError(f"文件不存在: {file_id}")

            # 访问记录先落库（流在请求返回后才被消费）
            repo.record_access(file_id)
            session.commit()

            info = FileInfo(
                file_id=db_file.id,
                original_name=db_file.original_name,
                storage_path=db_file.storage_path,
                file_type=db_file.file_type,
                extension=db_file.extension,
                mime_type=db_file.mime_type or "application/octet-stream",
                size=db_file.file_size,
                file_hash=db_file.file_hash,
                bucket=db_file.bucket,
                version=db_file.version,
                status=db_file.status,
                owner_id=db_file.owner_id,
                created_at=db_file.created_at,
            )
            chunks = self.backend.iter_file(db_file.storage_path)
            return chunks, info
        finally:
            session.close()

    def download_to_file(self, file_id: str, local_path: str) -> FileInfo:
        """下载到本地文件（供 Agent 使用）"""
        content, info = self.download(file_id)
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(content)
        return info

    def get_file_path(self, file_id: str) -> str:
        """
        获取文件的本地路径

        Agent 应优先使用 download_to_file 指定工作目录。
        """
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.get_by_id(file_id)
            if not db_file or db_file.status in {"deleted", "deleting"}:
                raise FileNotFoundError(f"文件不存在: {file_id}")

            if self.config.storage_type == "local":
                # 本地存储直接返回绝对路径
                backend = self.backend
                if hasattr(backend, "_full_path"):
                    return backend._full_path(db_file.storage_path)
                return db_file.storage_path
            else:
                # 远程存储下载到临时目录
                import tempfile
                tmp_dir = tempfile.gettempdir()
                hash_prefix = (db_file.file_hash or "nohash")[:12]
                local_path = os.path.join(
                    tmp_dir,
                    f"{file_id}-v{db_file.version}-{hash_prefix}{db_file.extension}",
                )
                if not os.path.exists(local_path):
                    self.download_to_file(file_id, local_path)
                return local_path
        finally:
            session.close()

    def get_url(self, file_id: str, expires: int = 3600) -> str:
        """获取文件访问 URL"""
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.get_by_id(file_id)
            if not db_file or db_file.status in {"deleted", "deleting"}:
                raise FileNotFoundError(f"文件不存在: {file_id}")
            return self.backend.get_url(db_file.storage_path, expires)
        finally:
            session.close()

    # ===== 查询 =====

    def get_info(self, file_id: str) -> FileInfo:
        """获取文件信息"""
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.get_by_id(file_id)
            if not db_file or db_file.status in {"deleted", "deleting"}:
                raise FileNotFoundError(f"文件不存在: {file_id}")
            return FileInfo(
                file_id=db_file.id,
                original_name=db_file.original_name,
                storage_path=db_file.storage_path,
                file_type=db_file.file_type,
                extension=db_file.extension,
                mime_type=db_file.mime_type,
                size=db_file.file_size,
                file_hash=db_file.file_hash,
                bucket=db_file.bucket,
                version=db_file.version,
                status=db_file.status,
                owner_id=db_file.owner_id,
                parent_file_id=db_file.parent_file_id,
                created_at=db_file.created_at,
                metadata=json.loads(db_file.metadata_json) if db_file.metadata_json else {},
            )
        finally:
            session.close()

    def list_files(self, owner_id: str | None = None, file_type: str | None = None,
                   bucket: str | None = None, offset: int = 0, limit: int = 100) -> List[FileInfo]:
        """列出文件"""
        from sqlalchemy import select, and_, ColumnElement
        session = self._get_session()
        try:
            from ..database.models import File
            conditions: list[ColumnElement[bool]] = [File.status.notin_(("deleted", "deleting"))]
            if owner_id:
                conditions.append(File.owner_id == owner_id)
            if file_type:
                conditions.append(File.file_type == file_type)
            if bucket:
                conditions.append(File.bucket == bucket)
            stmt = select(File).where(and_(*conditions)).order_by(
                File.created_at.desc()).offset(offset).limit(limit)
            db_files = list(session.execute(stmt).scalars().all())
            return [FileInfo(
                file_id=f.id, original_name=f.original_name,
                storage_path=f.storage_path, file_type=f.file_type,
                extension=f.extension, mime_type=f.mime_type,
                size=f.file_size, file_hash=f.file_hash,
                bucket=f.bucket, version=f.version, status=f.status,
                owner_id=f.owner_id, parent_file_id=f.parent_file_id,
                created_at=f.created_at,
                metadata=json.loads(f.metadata_json) if f.metadata_json else {},
            ) for f in db_files]
        finally:
            session.close()

    def count_files(self, owner_id: str | None = None, file_type: str | None = None,
                    bucket: str | None = None) -> int:
        """统计与 ``list_files`` 相同筛选条件下的未删除文件数。"""
        from sqlalchemy import select, and_, func, ColumnElement
        session = self._get_session()
        try:
            from ..database.models import File
            conditions: list[ColumnElement[bool]] = [File.status.notin_(("deleted", "deleting"))]
            if owner_id:
                conditions.append(File.owner_id == owner_id)
            if file_type:
                conditions.append(File.file_type == file_type)
            if bucket:
                conditions.append(File.bucket == bucket)
            stmt = select(func.count(File.id)).where(and_(*conditions))
            return int(session.execute(stmt).scalar() or 0)
        finally:
            session.close()

    def list_deleted_files(self, owner_id: str | None = None, file_type: str | None = None,
                           offset: int = 0, limit: int = 100) -> List[FileInfo]:
        """列出仍可恢复的软删除文件。永久删除中的 tombstone 不对用户展示。"""
        from sqlalchemy import select, and_
        session = self._get_session()
        try:
            from ..database.models import File
            conditions = [File.status == "deleted"]
            if owner_id:
                conditions.append(File.owner_id == owner_id)
            if file_type:
                conditions.append(File.file_type == file_type)
            stmt = select(File).where(and_(*conditions)).order_by(
                File.deleted_at.desc(), File.created_at.desc()
            ).offset(offset).limit(limit)
            db_files = list(session.execute(stmt).scalars().all())
            return [FileInfo(
                file_id=f.id, original_name=f.original_name,
                storage_path=f.storage_path, file_type=f.file_type,
                extension=f.extension, mime_type=f.mime_type,
                size=f.file_size, file_hash=f.file_hash,
                bucket=f.bucket, version=f.version, status=f.status,
                owner_id=f.owner_id, parent_file_id=f.parent_file_id,
                created_at=f.created_at, deleted_at=f.deleted_at,
                metadata=json.loads(f.metadata_json) if f.metadata_json else {},
            ) for f in db_files]
        finally:
            session.close()

    def count_deleted_files(self, owner_id: str | None = None,
                            file_type: str | None = None) -> int:
        """统计仍可恢复的软删除文件。"""
        from sqlalchemy import select, and_, func
        session = self._get_session()
        try:
            from ..database.models import File
            conditions = [File.status == "deleted"]
            if owner_id:
                conditions.append(File.owner_id == owner_id)
            if file_type:
                conditions.append(File.file_type == file_type)
            return int(session.execute(
                select(func.count(File.id)).where(and_(*conditions))
            ).scalar() or 0)
        finally:
            session.close()

    def exists(self, file_id: str) -> bool:
        """检查文件是否存在"""
        try:
            info = self.get_info(file_id)
            return self.backend.exists(info.storage_path)
        except FileNotFoundError:
            return False

    # ===== 删除 =====

    def delete(self, file_id: str, permanent: bool = False) -> bool:
        """
        删除文件

        Args:
            file_id: 文件ID
            permanent: True=物理删除, False=软删除（标记deleted）
        """
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.get_by_id(file_id)
            if not db_file:
                return False

            if permanent:
                # 先提交可重试的 tombstone，再删除物理内容。若物理删除或最终
                # DB 提交失败，记录会停在 deleting，后续可安全重试而不会
                # 重新把残缺文件暴露为 ready。
                repo.update_status(file_id, "deleting")
                session.commit()
                from ..database.repository import FileVersionRepository
                vrepo = FileVersionRepository(session)
                versions = vrepo.get_by_file(file_id)
                paths = {db_file.storage_path, *(v.storage_path for v in versions)}
                failed_paths = []
                for path in paths:
                    try:
                        self.backend.delete(path)
                    except Exception:
                        failed_paths.append(path)
                if failed_paths:
                    raise RuntimeError("永久删除未能清理全部物理内容，请重试")
                for version in versions:
                    session.delete(version)
                session.delete(db_file)
            else:
                repo.mark_deleted(file_id)

            session.commit()
            return True
        finally:
            session.close()

    def restore_deleted(self, file_id: str) -> FileInfo:
        """恢复软删除文件；物理内容缺失或删除已进入提交阶段时拒绝恢复。"""
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.get_by_id(file_id)
            if not db_file or db_file.status != "deleted":
                raise FileNotFoundError(f"回收站中不存在文件: {file_id}")
            if not self.backend.exists(db_file.storage_path):
                raise RuntimeError("文件内容已缺失，无法恢复；可尝试永久删除该记录")

            repo.restore_deleted(file_id)
            session.commit()
            return FileInfo(
                file_id=db_file.id, original_name=db_file.original_name,
                storage_path=db_file.storage_path, file_type=db_file.file_type,
                extension=db_file.extension, mime_type=db_file.mime_type,
                size=db_file.file_size, file_hash=db_file.file_hash,
                bucket=db_file.bucket, version=db_file.version,
                status=db_file.status, owner_id=db_file.owner_id,
                parent_file_id=db_file.parent_file_id,
                created_at=db_file.created_at,
                metadata=json.loads(db_file.metadata_json) if db_file.metadata_json else {},
            )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ===== 版本管理 =====

    def create_version(self, parent_file_id: str, content: bytes,
                       change_description: str | None = None,
                       changed_by: str = "agent") -> FileInfo:
        with self._version_lock(parent_file_id):
            return self._create_version_unlocked(
                parent_file_id, content, change_description, changed_by
            )

    def _create_version_unlocked(self, parent_file_id: str, content: bytes,
                                 change_description: str | None = None,
                                 changed_by: str = "agent") -> FileInfo:
        """
        创建文件新版本

        文件字节不可变原则：
        1. 当前内容归档到 versions/（版本记录指向归档路径）
        2. 新内容写入一个全新的存储路径
        3. 同一事务内把主文件记录切到新路径（storage_path/size/hash/version）
        任何一步失败都不会破坏旧文件：旧文件字节从未被覆盖。
        """
        session = self._get_session()
        try:
            from ..database.repository import FileRepository, FileVersionRepository
            repo = FileRepository(session)
            vrepo = FileVersionRepository(session)

            parent = repo.get_by_id(parent_file_id)
            if not parent or parent.status in {"deleted", "deleting"}:
                raise FileNotFoundError(f"父文件不存在: {parent_file_id}")

            # 校验新内容
            validate_size(content, self.config.max_file_size)
            new_hash = compute_hash(content)

            # 当前主路径本身已是不可变版本字节，直接把路径转为版本记录；
            # 不再复制一次后留下无人引用的旧主文件。
            old_storage_path = parent.storage_path
            vrepo.create_version(
                parent_file_id=parent_file_id,
                version_number=parent.version,
                storage_path=old_storage_path,
                file_size=parent.file_size,
                file_hash=parent.file_hash,
                change_description=parent.change_description,
                changed_by=changed_by,
            )

            # 新内容写入全新路径（绝不覆写旧版本路径）
            new_version_num = parent.version + 1
            new_file_id = generate_file_id()
            new_storage_path = generate_storage_path(
                parent.bucket, new_file_id, parent.extension,
            )
            uploaded_new = False
            try:
                self.backend.upload(new_storage_path, content, parent.mime_type)
                uploaded_new = True
                repo.update(parent_file_id, {
                    "storage_path": new_storage_path,
                    "file_size": len(content),
                    "file_hash": new_hash,
                    "version": new_version_num,
                    "change_description": change_description,
                    "status": "ready",
                })
                session.commit()
            except Exception:
                session.rollback()
                if uploaded_new:
                    try:
                        self.backend.delete(new_storage_path)
                    except Exception:
                        logger.exception("版本创建失败后清理新文件失败: %s", new_storage_path)
                raise

            return FileInfo(
                file_id=parent.id,
                original_name=parent.original_name,
                storage_path=new_storage_path,
                file_type=parent.file_type,
                extension=parent.extension,
                mime_type=parent.mime_type,
                size=len(content),
                file_hash=new_hash,
                bucket=parent.bucket,
                version=new_version_num,
                status="ready",
                owner_id=parent.owner_id,
                parent_file_id=parent.parent_file_id,
                change_description=change_description,
                metadata=json.loads(parent.metadata_json) if parent.metadata_json else {},
            )
        finally:
            session.close()

    def get_versions(self, file_id: str) -> List[dict]:
        """获取文件所有版本"""
        session = self._get_session()
        try:
            from ..database.repository import FileVersionRepository
            vrepo = FileVersionRepository(session)
            versions = vrepo.get_by_file(file_id)
            return [{
                "version_id": v.id,
                "version_number": v.version_number,
                "storage_path": v.storage_path,
                "file_size": v.file_size,
                "file_hash": v.file_hash,
                "change_description": v.change_description,
                "changed_by": v.changed_by,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            } for v in versions]
        finally:
            session.close()

    def restore_version(self, file_id: str, version_number: int) -> FileInfo:
        with self._version_lock(file_id):
            return self._restore_version_unlocked(file_id, version_number)

    def _restore_version_unlocked(self, file_id: str, version_number: int) -> FileInfo:
        """恢复到指定版本（把目标版本内容作为新版本写回，不覆写任何原字节）"""
        session = self._get_session()
        try:
            from ..database.repository import FileRepository, FileVersionRepository
            repo = FileRepository(session)
            vrepo = FileVersionRepository(session)

            parent = repo.get_by_id(file_id)
            if not parent:
                raise FileNotFoundError(f"文件不存在: {file_id}")

            versions = vrepo.get_by_file(file_id)
            target = next((v for v in versions if v.version_number == version_number), None)
            if not target:
                raise ValueError(f"版本不存在: v{version_number}")

            # 读取目标版本内容
            content = self.backend.download(target.storage_path)

            # 当前主路径直接成为历史版本；不复制、不遗留孤儿。
            current_storage_path = parent.storage_path
            vrepo.create_version(
                parent_file_id=file_id,
                version_number=parent.version,
                storage_path=current_storage_path,
                file_size=parent.file_size,
                file_hash=parent.file_hash,
                change_description=f"恢复前的版本 v{parent.version}",
                changed_by="restore",
            )

            # 恢复内容写入全新路径，再切换主记录
            new_file_id = generate_file_id()
            new_storage_path = generate_storage_path(
                parent.bucket, new_file_id, parent.extension,
            )
            uploaded_new = False
            try:
                self.backend.upload(new_storage_path, content, parent.mime_type)
                uploaded_new = True
                new_version = parent.version + 1
                repo.update(file_id, {
                    "storage_path": new_storage_path,
                    "file_size": target.file_size,
                    "file_hash": target.file_hash,
                    "version": new_version,
                    "change_description": f"从 v{version_number} 恢复",
                    "status": "ready",
                })
                session.commit()
            except Exception:
                session.rollback()
                if uploaded_new:
                    try:
                        self.backend.delete(new_storage_path)
                    except Exception:
                        logger.exception("版本恢复失败后清理新文件失败: %s", new_storage_path)
                raise

            return self.get_info(file_id)
        finally:
            session.close()

    # ===== 分片上传 =====

    def init_multipart_upload(self, filename: str, owner_id: str | None = None,
                               content_type: str | None = None,
                               expected_size: int | None = None,
                               expected_parts: int | None = None,
                               expected_sha256: str | None = None) -> dict:
        """初始化分片上传"""
        ext = validate_extension(filename)
        if not isinstance(expected_size, int) or expected_size < 1:
            raise ValueError("expected_size 必须是正整数")
        if expected_size > self.config.max_file_size:
            raise ValueError("文件总大小超过限制")
        if not isinstance(expected_parts, int) or expected_parts < 1:
            raise ValueError("expected_parts 必须是正整数")
        if expected_parts > expected_size:
            raise ValueError("expected_parts 不能大于文件字节数")
        if (not isinstance(expected_sha256, str)
                or len(expected_sha256) != 64
                or any(c not in "0123456789abcdefABCDEF" for c in expected_sha256)):
            raise ValueError("expected_sha256 必须是 64 位十六进制 SHA-256")
        file_id = generate_file_id()
        storage_path = generate_storage_path(BUCKET_UPLOADS, file_id, ext)

        upload_id = self.backend.init_multipart_upload(storage_path, content_type)

        # 记录到数据库（状态 uploading）
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.create_file(
                original_name=filename,
                file_type=get_file_category(filename),
                extension=ext,
                storage_path=storage_path,
                file_size=0,
                bucket=BUCKET_UPLOADS,
                storage_backend=self.config.storage_type,
                owner_id=owner_id,
                mime_type=content_type or get_mime_type(filename),
                metadata={
                    "upload_id": upload_id,
                    "multipart": True,
                    "expected_size": expected_size,
                    "expected_parts": expected_parts,
                    "expected_sha256": expected_sha256.lower(),
                },
            )
            repo.update_status(db_file.id, "uploading")
            session.commit()
            return {
                "file_id": db_file.id,
                "upload_id": upload_id,
                "storage_path": storage_path,
            }
        except Exception:
            session.rollback()
            self.backend.abort_multipart_upload(storage_path, upload_id)
            raise
        finally:
            session.close()

    @staticmethod
    def _verify_upload_binding(db_file, upload_id: str):
        """分片会话必须归属该 file_id：init 时写入 metadata 的 upload_id
        必须一致，防止用一个 upload_id 往别人 file_id 的存储路径写分片。
        metadata 里没有 upload_id 的记录（普通上传）一律拒绝。"""
        try:
            meta = json.loads(db_file.metadata_json or "{}")
        except (TypeError, ValueError):
            meta = {}
        bound = meta.get("upload_id")
        if bound != upload_id:
            raise ValueError("upload_id 与该文件不匹配")


    def upload_part(self, file_id: str, upload_id: str,
                    part_number: int, content: bytes) -> dict:
        """上传分片"""
        if part_number < 1:
            raise ValueError("part_number 必须从 1 开始")
        # 单片上限：防止无限制分片把任意大的请求体整读进内存
        if len(content) == 0:
            raise ValueError("分片内容为空")
        if len(content) > CHUNK_SIZE:
            raise ValueError(f"分片大小超过限制 {CHUNK_SIZE // 1024 // 1024}MB")
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.get_by_id(file_id)
            if not db_file:
                raise FileNotFoundError(f"文件不存在: {file_id}")
            if db_file.status != "uploading":
                raise ValueError(f"文件当前状态为 {db_file.status}，不允许上传分片")
            self._verify_upload_binding(db_file, upload_id)

            result = self.backend.upload_part(
                db_file.storage_path, upload_id, part_number, content,
            )
            return result
        finally:
            session.close()

    def _streaming_hash(self, storage_path: str) -> str:
        """流式计算文件哈希（避免大文件整读进内存）"""
        import hashlib
        h = hashlib.sha256()
        for chunk in self.backend.iter_file(storage_path):
            h.update(chunk)
        return h.hexdigest()

    def complete_multipart_upload(self, file_id: str, upload_id: str,
                                   parts: list) -> FileInfo:
        """完成分片上传"""
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.get_by_id(file_id)
            if not db_file:
                raise FileNotFoundError(f"文件不存在: {file_id}")
            if db_file.status != "uploading":
                raise ValueError(f"文件当前状态为 {db_file.status}，不允许完成分片上传")
            self._verify_upload_binding(db_file, upload_id)

            try:
                upload_meta = json.loads(db_file.metadata_json or "{}")
            except (TypeError, ValueError):
                upload_meta = {}
            expected_parts = upload_meta.get("expected_parts")
            expected_size = upload_meta.get("expected_size")
            expected_sha256 = upload_meta.get("expected_sha256")
            if not all((expected_parts, expected_size, expected_sha256)):
                raise ValueError("分片上传缺少完整性元数据，请重新初始化")

            if not parts:
                raise ValueError("分片列表不能为空")
            part_numbers = [p.get("part_number") for p in parts if isinstance(p, dict)]
            if (len(part_numbers) != len(parts)
                    or any(not isinstance(n, int) or n < 1 for n in part_numbers)
                    or len(set(part_numbers)) != len(part_numbers)):
                raise ValueError("分片序号必须是从 1 开始且不重复的整数")
            if sorted(n for n in part_numbers if isinstance(n, int)) != list(range(1, expected_parts + 1)):
                raise ValueError(f"分片必须从 1 连续到 {expected_parts}，不能缺片或多片")

            result = self.backend.complete_multipart_upload(
                db_file.storage_path, upload_id, parts,
            )

            def reject_completed_upload(message: str):
                try:
                    self.backend.delete(db_file.storage_path)
                finally:
                    repo.mark_deleted(file_id)
                    session.commit()
                raise ValueError(message)

            # 合并后总量必须仍受单文件大小上限约束（分片路径会绕过 upload 的校验）
            total_size = result.get("size", 0)
            if total_size != expected_size or total_size > self.config.max_file_size:
                reject_completed_upload(
                    f"文件总大小校验失败：期望 {expected_size} 字节，实际 {total_size} 字节"
                )

            file_hash = self._streaming_hash(db_file.storage_path)
            if file_hash.lower() != expected_sha256:
                reject_completed_upload("整文件 SHA-256 校验失败")

            # 分片链也必须执行与普通上传一致的内容类型校验。LocalStorage
            # 是当前唯一受支持后端，使用其受根目录约束的绝对路径读取。
            if isinstance(self.backend, LocalStorage):
                try:
                    with open(self.backend._full_path(db_file.storage_path), "rb") as merged:
                        validate_fileobj(
                            db_file.original_name, merged, self.config.max_file_size
                        )
                except FileValidationError as exc:
                    reject_completed_upload(str(exc))

            repo.update(file_id, {
                "file_size": total_size,
                "file_hash": file_hash,
                "status": "ready",
            })
            session.commit()

            return self.get_info(file_id)
        finally:
            session.close()

    def abort_multipart_upload(self, file_id: str, upload_id: str) -> bool:
        """取消分片上传"""
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.get_by_id(file_id)
            if db_file:
                self._verify_upload_binding(db_file, upload_id)
                self.backend.abort_multipart_upload(db_file.storage_path, upload_id)
                repo.mark_deleted(file_id)
                session.commit()
            return True
        finally:
            session.close()

    # ===== 生命周期管理 =====

    def cleanup_deleting(self, min_age_seconds: float | None = None,
                         limit: int = 500) -> dict:
        """收口停留在 deleting 中间态的记录（永久删除的 GC）。

        delete(permanent=True) 先写 tombstone 再删物理内容，两步之间失败或
        进程崩溃都会让记录永久停在 deleting：既不再对业务可见（所有查询都
        notin_(deleted, deleting)），又持续占着数据库行与磁盘内容。

        语义：
        - 只处理 status == "deleting"；ready/soft-deleted 的记录绝不触碰。
        - 物理内容还在 → 重试删除（backend.delete 对"已不存在"幂等）。
        - 物理内容已不存在 → 直接完成 DB 收口（删版本行 + 删主记录）。
        - 单条失败回滚并继续下一条，不阻断整批；失败记录保持 deleting 可重试。
        - 幂等：重复运行第二次扫到 0 条。
        """
        from ..database.time import utc_now

        if min_age_seconds is None:
            min_age_seconds = DELETING_GRACE_SECONDS
        try:
            min_age_seconds = max(0.0, float(min_age_seconds))
        except (TypeError, ValueError):
            min_age_seconds = DELETING_GRACE_SECONDS
        cutoff = utc_now() - timedelta(seconds=min_age_seconds)

        session = self._get_session()
        result = {"scanned": 0, "resolved": 0, "failed": 0, "freed_bytes": 0}
        try:
            from ..database.repository import FileRepository, FileVersionRepository
            repo = FileRepository(session)
            vrepo = FileVersionRepository(session)
            records = repo.get_deleting(older_than=cutoff, limit=limit)
            result["scanned"] = len(records)

            for record in records:
                record_id = record.id
                size = int(record.file_size or 0)
                try:
                    versions = vrepo.get_by_file(record_id)
                    paths = {record.storage_path, *(v.storage_path for v in versions)}
                    for path in sorted(p for p in paths if p and self.backend.exists(p)):
                        self.backend.delete(path)
                    # 物理内容已清空（或本就不存在）→ 完成永久删除的 DB 收口。
                    # FileVersion 没有 delete-orphan cascade，必须显式删除。
                    for version in versions:
                        session.delete(version)
                    session.delete(record)
                    session.commit()
                    result["resolved"] += 1
                    result["freed_bytes"] += size
                    logger.info("GC 收口 deleting 记录 %s（回收 %s 字节）", record_id, size)
                except Exception as e:
                    session.rollback()
                    result["failed"] += 1
                    # 保持 deleting：记录仍处于"可安全重试"状态，不假装修复成功
                    logger.warning("GC 处理 deleting 记录 %s 失败，保持可重试: %s",
                                   record_id, e)
            return result
        finally:
            session.close()

    def cleanup_temp_files(self, hours: int | None = None) -> dict:
        """清理过期临时文件"""
        if not hours or hours <= 0:
            hours = self.config.temp_expire_hours
        session = self._get_session()
        result = {"cleaned": 0, "freed_bytes": 0}
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            expired = repo.get_expired_temp_files(hours)

            for f in expired:
                try:
                    self.backend.delete(f.storage_path)
                    result["freed_bytes"] += f.file_size
                    repo.mark_deleted(f.id)
                    result["cleaned"] += 1
                except Exception as e:
                    logger.warning(f"清理临时文件失败 {f.id}: {e}")

            session.commit()
            logger.info(f"临时文件清理: 删除 {result['cleaned']} 个文件")
            return result
        finally:
            session.close()

    def archive_old_versions(self, keep: int | None = None) -> dict:
        """归档旧版本（保留最近 N 个）"""
        keep = keep or self.config.max_versions
        if keep < 1:
            raise ValueError("keep 必须至少为 1")
        session = self._get_session()
        result = {"archived": 0}
        try:
            from ..database.repository import FileVersionRepository
            from ..database.models import FileVersion
            vrepo = FileVersionRepository(session)

            # 按文件分组
            from sqlalchemy import select
            stmt = select(FileVersion.parent_file_id).distinct()
            parent_ids = [r[0] for r in session.execute(stmt).all()]

            for pid in parent_ids:
                versions = vrepo.get_by_file(pid)
                if len(versions) > keep:
                    # get_by_file 为升序：保留最新 keep 个，给更老版本写入真实
                    # archived_at 元数据。重复运行不会虚增计数。
                    for v in versions[:-keep]:
                        try:
                            meta = json.loads(v.metadata_json or "{}")
                        except (TypeError, ValueError):
                            meta = {}
                        if meta.get("archived_at"):
                            continue
                        meta["archived_at"] = datetime.now(timezone.utc).isoformat()
                        v.metadata_json = json.dumps(meta, ensure_ascii=False)
                        result["archived"] += 1

            session.commit()

            return result
        finally:
            session.close()

    def get_storage_stats(self) -> dict:
        """存储统计"""
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            stats = repo.get_storage_stats()
            stats["storage_type"] = self.config.storage_type
            if self.config.storage_type == "local":
                stats["storage_path"] = self.config.local_path
            return stats
        finally:
            session.close()


# 全局单例
_storage_service: Optional[StorageService] = None


def get_storage_service() -> StorageService:
    """获取全局 StorageService 单例"""
    global _storage_service
    if _storage_service is None:
        _storage_service = StorageService()
    return _storage_service


def reconcile_pending_deletions(**kwargs) -> dict:
    """对全局单例执行一次 deleting GC（启动期收口入口，供 startup 调用）。"""
    return get_storage_service().cleanup_deleting(**kwargs)
