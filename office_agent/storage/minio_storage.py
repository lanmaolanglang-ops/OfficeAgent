"""
MinIO 对象存储实现

依赖：pip install minio
配置：
    STORAGE_TYPE=minio
    MINIO_ENDPOINT=localhost:9000
    MINIO_ACCESS_KEY=minioadmin
    MINIO_SECRET_KEY=minioadmin
    MINIO_BUCKET=office-agent
    MINIO_SECURE=false
"""
import io
import logging
from typing import BinaryIO

from .storage_backend import StorageBackend

logger = logging.getLogger("office_agent.storage.minio")


class MinioStorage(StorageBackend):
    """
    MinIO 对象存储

    使用 MinIO Python SDK，兼容 S3 协议。
    """

    def __init__(self, endpoint: str, access_key: str, secret_key: str,
                 bucket: str = "office-agent", secure: bool = False,
                 region: str = None):
        try:
            from minio import Minio
        except ImportError:
            raise ImportError("使用 MinIO 存储需要安装 minio: pip install minio")

        self.client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=region,
        )
        self.bucket = bucket
        self._ensure_bucket()

    def _ensure_bucket(self):
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
            logger.info(f"创建 MinIO bucket: {self.bucket}")

    def upload(self, storage_path: str, content: bytes,
               content_type: str = None) -> dict:
        result = self.client.put_object(
            self.bucket, storage_path,
            io.BytesIO(content), len(content),
            content_type=content_type or "application/octet-stream",
        )
        return {"path": storage_path, "size": len(content), "etag": result.etag}

    def upload_fileobj(self, storage_path: str, fileobj: BinaryIO,
                       content_type: str = None) -> dict:
        import os
        # 需要知道大小
        pos = fileobj.tell()
        fileobj.seek(0, 2)
        size = fileobj.tell() - pos
        fileobj.seek(pos)

        result = self.client.put_object(
            self.bucket, storage_path, fileobj, size,
            content_type=content_type or "application/octet-stream",
        )
        return {"path": storage_path, "size": size, "etag": result.etag}

    def download(self, storage_path: str) -> bytes:
        response = self.client.get_object(self.bucket, storage_path)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def download_fileobj(self, storage_path: str, fileobj: BinaryIO) -> None:
        response = self.client.get_object(self.bucket, storage_path)
        try:
            for chunk in response.stream(8192):
                fileobj.write(chunk)
        finally:
            response.close()
            response.release_conn()

    def delete(self, storage_path: str) -> bool:
        self.client.remove_object(self.bucket, storage_path)
        return True

    def exists(self, storage_path: str) -> bool:
        from minio.error import S3Error
        try:
            self.client.stat_object(self.bucket, storage_path)
            return True
        except S3Error:
            return False

    def size(self, storage_path: str) -> int:
        stat = self.client.stat_object(self.bucket, storage_path)
        return stat.size

    def get_url(self, storage_path: str, expires: int = 3600) -> str:
        from datetime import timedelta
        url = self.client.presigned_get_object(
            self.bucket, storage_path,
            expires=timedelta(seconds=expires),
        )
        return url

    def copy(self, src_path: str, dst_path: str) -> dict:
        from minio.commonconfig import CopySource
        result = self.client.copy_object(
            self.bucket, dst_path,
            CopySource(self.bucket, src_path),
        )
        stat = self.client.stat_object(self.bucket, dst_path)
        return {"path": dst_path, "size": stat.size, "etag": result.etag}

    def list_files(self, prefix: str = "", recursive: bool = True) -> list:
        objects = self.client.list_objects(
            self.bucket, prefix=prefix, recursive=recursive,
        )
        return [{
            "path": obj.object_name,
            "size": obj.size,
            "modified": obj.last_modified.isoformat() if obj.last_modified else None,
        } for obj in objects]

    def init_multipart_upload(self, storage_path: str,
                               content_type: str = None) -> str:
        result = self.client._create_multipart_upload(
            self.bucket, storage_path,
            {"Content-Type": content_type or "application/octet-stream"},
        )
        return result

    def upload_part(self, storage_path: str, upload_id: str,
                    part_number: int, content: bytes) -> dict:
        result = self.client._upload_part(
            self.bucket, storage_path, content,
            upload_id=upload_id, part_number=part_number,
        )
        return {"part_number": part_number, "etag": result}

    def complete_multipart_upload(self, storage_path: str, upload_id: str,
                                  parts: list) -> dict:
        from minio.commonconfig import COMPOSE
        result = self.client._complete_multipart_upload(
            self.bucket, storage_path, upload_id,
            [{"partNumber": p["part_number"], "etag": p["etag"]} for p in parts],
        )
        stat = self.client.stat_object(self.bucket, storage_path)
        return {"path": storage_path, "size": stat.size, "etag": result}

    def abort_multipart_upload(self, storage_path: str, upload_id: str) -> bool:
        self.client._abort_multipart_upload(self.bucket, storage_path, upload_id)
        return True
