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
import shutil
from datetime import datetime, timedelta
from typing import Optional, BinaryIO, Tuple, List, Dict

from .storage_backend import StorageBackend
from .local_storage import LocalStorage
from .validators import (
    validate_file, validate_extension, validate_size, get_file_category,
    get_mime_type, compute_hash, compute_file_hash, FileValidationError,
    DEFAULT_MAX_SIZE,
)
from .path_generator import (
    generate_file_id, generate_version_id, generate_storage_path,
    generate_temp_path, BUCKET_UPLOADS, BUCKET_OUTPUTS, BUCKET_TEMP,
    BUCKET_CACHE, BUCKET_VERSIONS,
)

logger = logging.getLogger("office_agent.storage")


class StorageConfig:
    """存储配置"""
    def __init__(self, storage_type: str = "local",
                 local_path: str = None,
                 minio_endpoint: str = None, minio_access_key: str = None,
                 minio_secret_key: str = None, minio_bucket: str = "office-agent",
                 minio_secure: bool = False,
                 s3_bucket: str = None, s3_access_key: str = None,
                 s3_secret_key: str = None, s3_region: str = None,
                 s3_endpoint_url: str = None,
                 max_file_size: int = DEFAULT_MAX_SIZE,
                 temp_expire_hours: int = 24,
                 max_versions: int = 10):
        self.storage_type = storage_type
        self.local_path = local_path or os.path.expanduser("~/.office_agent/storage")
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
    elif config.storage_type == "minio":
        from .minio_storage import MinioStorage
        return MinioStorage(
            endpoint=config.minio_endpoint,
            access_key=config.minio_access_key,
            secret_key=config.minio_secret_key,
            bucket=config.minio_bucket,
            secure=config.minio_secure,
        )
    elif config.storage_type == "s3":
        from .s3_storage import S3Storage
        return S3Storage(
            bucket=config.s3_bucket,
            aws_access_key_id=config.s3_access_key,
            aws_secret_access_key=config.s3_secret_key,
            region=config.s3_region,
            endpoint_url=config.s3_endpoint_url,
        )
    else:
        raise ValueError(f"不支持的存储类型: {config.storage_type}")


class FileInfo:
    """文件信息（业务对象）"""
    def __init__(self, file_id: str, original_name: str, storage_path: str,
                 file_type: str, extension: str, mime_type: str,
                 size: int, file_hash: str = None, bucket: str = "uploads",
                 version: int = 1, status: str = "ready",
                 owner_id: str = None, parent_file_id: str = None,
                 change_description: str = None,
                 created_at: datetime = None, metadata: dict = None):
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
        self.created_at = created_at or datetime.now()
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
            "metadata": self.metadata,
        }


class StorageService:
    """
    文件存储服务

    Agent 和 API 的统一文件访问入口。
    """

    def __init__(self, config: StorageConfig = None, backend: StorageBackend = None):
        self.config = config or StorageConfig.from_env()
        self.backend = backend or create_storage_backend(self.config)
        logger.info(f"StorageService 初始化: type={self.config.storage_type}")

    def _get_session(self):
        """获取数据库 session"""
        from ..database.session import SessionLocal
        return SessionLocal()

    # ===== 上传 =====

    def upload(self, filename: str, content: bytes,
               owner_id: str = None, bucket: str = BUCKET_UPLOADS,
               metadata: dict = None) -> FileInfo:
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
        finally:
            session.close()

    def upload_fileobj(self, filename: str, fileobj: BinaryIO,
                       owner_id: str = None, bucket: str = BUCKET_UPLOADS,
                       metadata: dict = None) -> FileInfo:
        """从文件对象上传"""
        content = fileobj.read()
        return self.upload(filename, content, owner_id, bucket, metadata)

    def save_output(self, source_path: str, original_name: str = None,
                    owner_id: str = None, change_description: str = None,
                    parent_file_id: str = None) -> FileInfo:
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

    def save_new_output(self, source_path: str, original_name: str = None,
                        owner_id: str = None, change_description: str = None) -> FileInfo:
        """
        保存 Agent 生成的独立输出文件（不发生版本覆盖）。

        与 save_output 的区别:
        - 始终写入 outputs 储存桶
        - 创建新的 File 记录和新 file_id
        - 不使用 parent_file_id，不触发版本化逻辑
        - 输入文件记录保持不变
        """
        return self.save_output(
            source_path=source_path,
            original_name=original_name,
            owner_id=owner_id,
            change_description=change_description,
            parent_file_id=None,
        )

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
            if not db_file or db_file.status == "deleted":
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

        注意：如果是远程存储（MinIO/S3），会下载到临时目录。
        Agent 应优先使用 download_to_file 指定工作目录。
        """
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.get_by_id(file_id)
            if not db_file or db_file.status == "deleted":
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
                local_path = os.path.join(tmp_dir, f"{file_id}{db_file.extension}")
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
            if not db_file or db_file.status == "deleted":
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
            if not db_file or db_file.status == "deleted":
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

    def list_files(self, owner_id: str = None, file_type: str = None,
                   bucket: str = None, offset: int = 0, limit: int = 100) -> List[FileInfo]:
        """列出文件"""
        from sqlalchemy import select, and_
        session = self._get_session()
        try:
            from ..database.models import File
            conditions = [File.status != "deleted"]
            if owner_id:
                conditions.append(File.owner_id == owner_id)
            if file_type:
                conditions.append(File.file_type == file_type)
            if bucket:
                conditions.append(File.bucket == bucket)
            stmt = select(File).where(and_(*conditions)).offset(offset).limit(limit)
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
                self.backend.delete(db_file.storage_path)
                # 同时删除版本文件
                from ..database.repository import FileVersionRepository
                vrepo = FileVersionRepository(session)
                versions = vrepo.get_by_file(file_id)
                for v in versions:
                    self.backend.delete(v.storage_path)
                session.delete(db_file)
            else:
                repo.mark_deleted(file_id)

            session.commit()
            return True
        finally:
            session.close()

    # ===== 版本管理 =====

    def create_version(self, parent_file_id: str, content: bytes,
                       change_description: str = None,
                       changed_by: str = "agent") -> FileInfo:
        """
        创建文件新版本

        流程：
        1. 获取父文件信息
        2. 将当前版本内容保存到 versions/
        3. 上传新版本内容到主路径
        4. 更新主文件记录
        """
        session = self._get_session()
        try:
            from ..database.repository import FileRepository, FileVersionRepository
            repo = FileRepository(session)
            vrepo = FileVersionRepository(session)

            parent = repo.get_by_id(parent_file_id)
            if not parent or parent.status == "deleted":
                raise FileNotFoundError(f"父文件不存在: {parent_file_id}")

            # 校验新内容
            validate_size(content, self.config.max_file_size)
            new_hash = compute_hash(content)

            # 1. 将当前版本归档到 versions/
            old_content = self.backend.download(parent.storage_path)
            version_id = generate_version_id()
            version_path = generate_storage_path(
                BUCKET_VERSIONS, version_id, parent.extension,
                parent_file_id=parent_file_id,
            )
            self.backend.upload(version_path, old_content, parent.mime_type)

            # 2. 记录版本
            new_version_num = parent.version + 1
            vrepo.create_version(
                parent_file_id=parent_file_id,
                version_number=parent.version,  # 归档的是旧版本号
                storage_path=version_path,
                file_size=parent.file_size,
                file_hash=parent.file_hash,
                change_description=parent.change_description,
                changed_by=changed_by,
            )

            # 3. 上传新版本到主路径
            self.backend.upload(parent.storage_path, content, parent.mime_type)

            # 4. 更新主文件
            repo.update(parent_file_id, {
                "file_size": len(content),
                "file_hash": new_hash,
                "version": new_version_num,
                "change_description": change_description,
                "status": "ready",
            })
            session.commit()

            return FileInfo(
                file_id=parent.id,
                original_name=parent.original_name,
                storage_path=parent.storage_path,
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
        """恢复到指定版本"""
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

            # 当前版本也归档
            current_content = self.backend.download(parent.storage_path)
            vid = generate_version_id()
            vpath = generate_storage_path(BUCKET_VERSIONS, vid, parent.extension,
                                          parent_file_id=file_id)
            self.backend.upload(vpath, current_content, parent.mime_type)
            vrepo.create_version(
                parent_file_id=file_id,
                version_number=parent.version,
                storage_path=vpath,
                file_size=parent.file_size,
                file_hash=parent.file_hash,
                change_description=f"恢复前的版本 v{parent.version}",
                changed_by="restore",
            )

            # 恢复目标版本
            self.backend.upload(parent.storage_path, content, parent.mime_type)
            new_version = parent.version + 1
            repo.update(file_id, {
                "file_size": target.file_size,
                "file_hash": target.file_hash,
                "version": new_version,
                "change_description": f"从 v{version_number} 恢复",
            })
            session.commit()

            return self.get_info(file_id)
        finally:
            session.close()

    # ===== 分片上传 =====

    def init_multipart_upload(self, filename: str, owner_id: str = None,
                               content_type: str = None) -> dict:
        """初始化分片上传"""
        ext = validate_extension(filename)
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
                metadata={"upload_id": upload_id, "multipart": True},
            )
            repo.update_status(db_file.id, "uploading")
            session.commit()
            return {
                "file_id": db_file.id,
                "upload_id": upload_id,
                "storage_path": storage_path,
            }
        finally:
            session.close()

    def upload_part(self, file_id: str, upload_id: str,
                    part_number: int, content: bytes) -> dict:
        """上传分片"""
        session = self._get_session()
        try:
            from ..database.repository import FileRepository
            repo = FileRepository(session)
            db_file = repo.get_by_id(file_id)
            if not db_file:
                raise FileNotFoundError(f"文件不存在: {file_id}")

            result = self.backend.upload_part(
                db_file.storage_path, upload_id, part_number, content,
            )
            return result
        finally:
            session.close()

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

            result = self.backend.complete_multipart_upload(
                db_file.storage_path, upload_id, parts,
            )

            # 计算哈希
            content = self.backend.download(db_file.storage_path)
            file_hash = compute_hash(content)

            repo.update(file_id, {
                "file_size": result.get("size", len(content)),
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
                self.backend.abort_multipart_upload(db_file.storage_path, upload_id)
                repo.mark_deleted(file_id)
                session.commit()
            return True
        finally:
            session.close()

    # ===== 生命周期管理 =====

    def cleanup_temp_files(self, hours: int = None) -> dict:
        """清理过期临时文件"""
        hours = hours or self.config.temp_expire_hours
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

    def archive_old_versions(self, keep: int = None) -> dict:
        """归档旧版本（保留最近 N 个）"""
        keep = keep or self.config.max_versions
        session = self._get_session()
        result = {"archived": 0}
        try:
            from ..database.repository import FileVersionRepository
            from ..database.models import FileVersion
            vrepo = FileVersionRepository(session)

            # 按文件分组
            from sqlalchemy import select, func
            stmt = select(FileVersion.parent_file_id).distinct()
            parent_ids = [r[0] for r in session.execute(stmt).all()]

            for pid in parent_ids:
                versions = vrepo.get_by_file(pid)
                if len(versions) > keep:
                    for v in versions[keep:]:
                        # 旧版本标记归档（不删除文件，节省空间可后续清理）
                        pass
                    result["archived"] += max(0, len(versions) - keep)

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
