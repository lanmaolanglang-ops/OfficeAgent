"""
存储后端抽象接口

所有存储实现（本地文件系统）必须实现此接口。
"""
import abc
from typing import BinaryIO, Optional, Iterator


class StorageBackend(abc.ABC):
    """
    存储后端抽象基类

    统一文件操作接口，支持本地文件系统。
    """

    @abc.abstractmethod
    def upload(self, storage_path: str, content: bytes,
               content_type: str = None) -> dict:
        """
        上传文件

        Args:
            storage_path: 存储路径（相对路径，如 uploads/2026/07/31/file_xxx.docx）
            content: 文件内容
            content_type: MIME 类型

        Returns:
            {"path": ..., "size": ..., "etag": ...}
        """
        pass

    @abc.abstractmethod
    def upload_fileobj(self, storage_path: str, fileobj: BinaryIO,
                       content_type: str = None) -> dict:
        """
        从文件对象上传（用于大文件/流式上传）
        """
        pass

    @abc.abstractmethod
    def download(self, storage_path: str) -> bytes:
        """
        下载文件内容

        Returns:
            文件字节内容
        """
        pass

    @abc.abstractmethod
    def download_fileobj(self, storage_path: str, fileobj: BinaryIO) -> None:
        """
        下载到文件对象（流式）
        """
        pass

    @abc.abstractmethod
    def delete(self, storage_path: str) -> bool:
        """
        删除文件

        Returns:
            是否成功
        """
        pass

    @abc.abstractmethod
    def exists(self, storage_path: str) -> bool:
        """检查文件是否存在"""
        pass

    @abc.abstractmethod
    def size(self, storage_path: str) -> int:
        """获取文件大小（字节）"""
        pass

    @abc.abstractmethod
    def get_url(self, storage_path: str, expires: int = 3600) -> str:
        """
        获取访问 URL

        Args:
            storage_path: 存储路径
            expires: 预签名 URL 过期时间（秒）

        Returns:
            可访问的 URL
        """
        pass

    @abc.abstractmethod
    def copy(self, src_path: str, dst_path: str) -> dict:
        """
        复制文件

        Returns:
            目标文件信息
        """
        pass

    @abc.abstractmethod
    def list_files(self, prefix: str = "", recursive: bool = True) -> list:
        """
        列出文件

        Returns:
            [{"path": ..., "size": ..., "modified": ...}, ...]
        """
        pass

    @abc.abstractmethod
    def init_multipart_upload(self, storage_path: str,
                               content_type: str = None) -> str:
        """
        初始化分片上传

        Returns:
            upload_id
        """
        pass

    @abc.abstractmethod
    def upload_part(self, storage_path: str, upload_id: str,
                    part_number: int, content: bytes) -> dict:
        """
        上传分片

        Returns:
            {"part_number": ..., "etag": ...}
        """
        pass

    @abc.abstractmethod
    def complete_multipart_upload(self, storage_path: str, upload_id: str,
                                  parts: list) -> dict:
        """
        完成分片上传

        Args:
            parts: [{"part_number": ..., "etag": ...}, ...]
        """
        pass

    @abc.abstractmethod
    def abort_multipart_upload(self, storage_path: str, upload_id: str) -> bool:
        """取消分片上传"""
        pass
