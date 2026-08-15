"""
AWS S3 / S3 兼容存储实现

依赖：pip install boto3
配置：
    STORAGE_TYPE=s3
    S3_BUCKET=office-agent
    AWS_ACCESS_KEY_ID=...
    AWS_SECRET_ACCESS_KEY=...
    AWS_REGION=us-east-1
    S3_ENDPOINT_URL=  # 可选，用于兼容其他S3服务
"""
import io
import logging
from typing import BinaryIO

from .storage_backend import StorageBackend

logger = logging.getLogger("office_agent.storage.s3")


class S3Storage(StorageBackend):
    """
    AWS S3 / S3 兼容存储

    使用 boto3，可用于 AWS S3、阿里云 OSS、腾讯云 COS 等 S3 兼容服务。
    """

    def __init__(self, bucket: str, aws_access_key_id: str = None,
                 aws_secret_access_key: str = None, region: str = None,
                 endpoint_url: str = None):
        try:
            import boto3
        except ImportError:
            raise ImportError("使用 S3 存储需要安装 boto3: pip install boto3")

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region,
            endpoint_url=endpoint_url,
        )
        self._ensure_bucket()

    def _ensure_bucket(self):
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            try:
                self.client.create_bucket(Bucket=self.bucket)
                logger.info(f"创建 S3 bucket: {self.bucket}")
            except Exception as e:
                logger.warning(f"Bucket 检查/创建失败（可能已存在或无权限）: {e}")

    def upload(self, storage_path: str, content: bytes,
               content_type: str = None) -> dict:
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        self.client.put_object(
            Bucket=self.bucket, Key=storage_path,
            Body=content, **extra,
        )
        return {"path": storage_path, "size": len(content)}

    def upload_fileobj(self, storage_path: str, fileobj: BinaryIO,
                       content_type: str = None) -> dict:
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        import os
        pos = fileobj.tell()
        self.client.upload_fileobj(fileobj, self.bucket, storage_path, ExtraArgs=extra)
        fileobj.seek(0, 2)
        size = fileobj.tell() - pos
        fileobj.seek(pos)
        return {"path": storage_path, "size": size}

    def download(self, storage_path: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=storage_path)
        return response["Body"].read()

    def download_fileobj(self, storage_path: str, fileobj: BinaryIO) -> None:
        self.client.download_fileobj(self.bucket, storage_path, fileobj)

    def delete(self, storage_path: str) -> bool:
        self.client.delete_object(Bucket=self.bucket, Key=storage_path)
        return True

    def exists(self, storage_path: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=storage_path)
            return True
        except Exception:
            return False

    def size(self, storage_path: str) -> int:
        response = self.client.head_object(Bucket=self.bucket, Key=storage_path)
        return response["ContentLength"]

    def get_url(self, storage_path: str, expires: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": storage_path},
            ExpiresIn=expires,
        )

    def copy(self, src_path: str, dst_path: str) -> dict:
        self.client.copy_object(
            Bucket=self.bucket, Key=dst_path,
            CopySource={"Bucket": self.bucket, "Key": src_path},
        )
        return {"path": dst_path}

    def list_files(self, prefix: str = "", recursive: bool = True) -> list:
        delimiter = "" if recursive else "/"
        paginator = self.client.get_paginator("list_objects_v2")
        result = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix,
                                        Delimiter=delimiter):
            for obj in page.get("Contents", []):
                result.append({
                    "path": obj["Key"],
                    "size": obj["Size"],
                    "modified": obj["LastModified"].isoformat(),
                })
        return result

    def init_multipart_upload(self, storage_path: str,
                               content_type: str = None) -> str:
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        response = self.client.create_multipart_upload(
            Bucket=self.bucket, Key=storage_path, **extra,
        )
        return response["UploadId"]

    def upload_part(self, storage_path: str, upload_id: str,
                    part_number: int, content: bytes) -> dict:
        response = self.client.upload_part(
            Bucket=self.bucket, Key=storage_path,
            UploadId=upload_id, PartNumber=part_number,
            Body=content,
        )
        return {"part_number": part_number, "etag": response["ETag"].strip('"')}

    def complete_multipart_upload(self, storage_path: str, upload_id: str,
                                  parts: list) -> dict:
        self.client.complete_multipart_upload(
            Bucket=self.bucket, Key=storage_path, UploadId=upload_id,
            MultipartUpload={
                "Parts": [
                    {"PartNumber": p["part_number"], "ETag": p["etag"]}
                    for p in sorted(parts, key=lambda x: x["part_number"])
                ]
            },
        )
        return {"path": storage_path}

    def abort_multipart_upload(self, storage_path: str, upload_id: str) -> bool:
        self.client.abort_multipart_upload(
            Bucket=self.bucket, Key=storage_path, UploadId=upload_id,
        )
        return True
