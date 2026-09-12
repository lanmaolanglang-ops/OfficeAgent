"""
本地文件系统存储实现
"""
import os
import re
import uuid
import shutil
import hashlib
from datetime import datetime
from typing import BinaryIO
from pathlib import Path, PureWindowsPath

from .storage_backend import StorageBackend
from .path_generator import ALL_BUCKETS


class LocalStorage(StorageBackend):
    """
    本地文件系统存储

    目录结构：
        root_path/
        ├── uploads/
        ├── outputs/
        ├── temp/
        ├── cache/
        ├── versions/
        └── multipart/  # 分片上传临时目录
    """

    def __init__(self, root_path: str, base_url: str = "/api/file/download"):
        self.root_path = os.path.abspath(os.path.expanduser(root_path))
        self.base_url = base_url
        self._ensure_dirs()

    def _ensure_dirs(self):
        """确保所有存储目录存在"""
        for bucket in ALL_BUCKETS:
            os.makedirs(os.path.join(self.root_path, bucket), exist_ok=True)
        os.makedirs(os.path.join(self.root_path, "multipart"), exist_ok=True)

    def _full_path(self, storage_path: str) -> str:
        """将相对路径转为绝对路径，防止路径穿越"""
        # 持久化路径统一使用目录分隔语义。旧 Windows 数据中的反斜杠在
        # Linux CI/恢复环境中不能被当作普通文件名，否则会定位到错误文件。
        windows_path = PureWindowsPath(storage_path)
        if windows_path.drive or windows_path.root:
            raise ValueError(f"非法路径: {storage_path}")
        portable_path = storage_path.replace("\\", os.sep).replace("/", os.sep)
        full = os.path.normpath(os.path.join(self.root_path, portable_path))
        root = os.path.normpath(self.root_path)
        # 安全检查：必须在 root_path 下（用 commonpath 防止前缀目录名伪造，
        # 例如 root=...\\storage 时 ...\\storage_evil\\x 不再被放行）
        try:
            if os.path.commonpath([full, root]) != root:
                raise ValueError(f"非法路径: {storage_path}")
        except ValueError as exc:
            if "非法路径" in str(exc):
                raise
            raise ValueError(f"非法路径: {storage_path}") from exc
        return full

    def _ensure_parent(self, full_path: str):
        """确保父目录存在"""
        parent = os.path.dirname(full_path)
        os.makedirs(parent, exist_ok=True)

    def _atomic_write(self, full_path: str, writer) -> None:
        """先写临时文件再原子替换，避免半写文件被当作正常文件"""
        self._ensure_parent(full_path)
        tmp_path = f"{full_path}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        try:
            with open(tmp_path, "wb") as f:
                writer(f)
            os.replace(tmp_path, full_path)
        except BaseException:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise

    def upload(self, storage_path: str, content: bytes,
               content_type: str | None = None) -> dict:
        full_path = self._full_path(storage_path)
        self._atomic_write(full_path, lambda f: f.write(content))
        return {
            "path": storage_path,
            "size": len(content),
            "etag": hashlib.md5(content).hexdigest(),
        }

    def upload_fileobj(self, storage_path: str, fileobj: BinaryIO,
                       content_type: str | None = None) -> dict:
        full_path = self._full_path(storage_path)
        size = 0
        md5 = hashlib.md5()

        def _copy(f):
            nonlocal size
            while True:
                chunk = fileobj.read(8192)
                if not chunk:
                    break
                f.write(chunk)
                md5.update(chunk)
                size += len(chunk)

        self._atomic_write(full_path, _copy)
        return {"path": storage_path, "size": size, "etag": md5.hexdigest()}

    def download(self, storage_path: str) -> bytes:
        full_path = self._full_path(storage_path)
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"文件不存在: {storage_path}")
        with open(full_path, "rb") as f:
            return f.read()

    def iter_file(self, storage_path: str, chunk_size: int = 1024 * 1024):
        """分块迭代文件内容（避免大文件整读进内存）"""
        full_path = self._full_path(storage_path)
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"文件不存在: {storage_path}")

        def _gen():
            with open(full_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        return _gen()

    def download_fileobj(self, storage_path: str, fileobj: BinaryIO) -> None:
        full_path = self._full_path(storage_path)
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"文件不存在: {storage_path}")
        with open(full_path, "rb") as f:
            shutil.copyfileobj(f, fileobj)

    def delete(self, storage_path: str) -> bool:
        full_path = self._full_path(storage_path)
        if os.path.exists(full_path):
            os.remove(full_path)
            return True
        return False

    def exists(self, storage_path: str) -> bool:
        return os.path.exists(self._full_path(storage_path))

    def size(self, storage_path: str) -> int:
        full_path = self._full_path(storage_path)
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"文件不存在: {storage_path}")
        return os.path.getsize(full_path)

    def get_url(self, storage_path: str, expires: int = 3600) -> str:
        # 本地存储返回 API 下载链接
        file_id = Path(storage_path).stem
        return f"{self.base_url}/{file_id}"

    def copy(self, src_path: str, dst_path: str) -> dict:
        src_full = self._full_path(src_path)
        dst_full = self._full_path(dst_path)
        self._ensure_parent(dst_full)
        shutil.copy2(src_full, dst_full)
        return {
            "path": dst_path,
            "size": os.path.getsize(dst_full),
        }

    def list_files(self, prefix: str = "", recursive: bool = True) -> list:
        search_path = self._full_path(prefix) if prefix else self.root_path
        results: list[dict[str, str | int]] = []

        if not os.path.exists(search_path):
            return results

        if os.path.isfile(search_path):
            stat = os.stat(search_path)
            return [{
                "path": os.path.relpath(search_path, self.root_path).replace("\\", "/"),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }]

        for root, dirs, files in os.walk(search_path):
            for fname in files:
                fpath = os.path.join(root, fname)
                stat = os.stat(fpath)
                rel = os.path.relpath(fpath, self.root_path).replace("\\", "/")
                results.append({
                    "path": rel,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
            if not recursive:
                break

        return results

    # === 分片上传 ===

    # 分片编号硬上限：与 S3/GCS 等对象存储协议一致（10000 片），
    # 防止极端编号制造稀疏编号空间。
    MAX_PART_NUMBER = 10000
    # init_multipart_upload 生成的 upload_id 形如 mp_<16位hex>；
    # 核心层校验它，防止内部调用方以路径穿越写任意位置。
    _UPLOAD_ID_RE = re.compile(r"^mp_[0-9a-f]{16}$")

    def _multipart_dir(self, upload_id: str) -> str:
        if not isinstance(upload_id, str) or not self._UPLOAD_ID_RE.match(upload_id):
            raise ValueError(f"无效的 upload_id: {upload_id!r}")
        return os.path.join(self.root_path, "multipart", upload_id)

    @staticmethod
    def _validate_part_number(part_number) -> int:
        """核心层不变量：协议 1-based（API 契约"分片序号，从1开始"）。

        bool 是 int 子类，必须显式拒绝；上界采用对象存储协议标准。
        """
        if isinstance(part_number, bool) or not isinstance(part_number, int):
            raise ValueError("part_number 必须是整数")
        if part_number < 1:
            raise ValueError("part_number 必须从 1 开始")
        if part_number > LocalStorage.MAX_PART_NUMBER:
            raise ValueError(
                f"part_number 超过上限 {LocalStorage.MAX_PART_NUMBER}")
        return part_number

    def init_multipart_upload(self, storage_path: str,
                               content_type: str | None = None) -> str:
        import uuid
        upload_id = f"mp_{uuid.uuid4().hex[:16]}"
        mp_dir = self._multipart_dir(upload_id)
        os.makedirs(mp_dir, exist_ok=True)
        # 保存目标路径
        with open(os.path.join(mp_dir, ".meta"), "w") as f:
            f.write(storage_path)
        return upload_id

    def upload_part(self, storage_path: str, upload_id: str,
                    part_number: int, content: bytes) -> dict:
        part_number = self._validate_part_number(part_number)
        if not isinstance(content, (bytes, bytearray)):
            raise ValueError("分片内容必须是字节")
        mp_dir = self._multipart_dir(upload_id)
        if not os.path.exists(mp_dir):
            raise ValueError(f"无效的 upload_id: {upload_id}")
        part_path = os.path.join(mp_dir, f"part_{part_number:05d}")
        with open(part_path, "wb") as f:
            f.write(content)
        return {
            "part_number": part_number,
            "etag": hashlib.md5(content).hexdigest(),
            "size": len(content),
        }

    def complete_multipart_upload(self, storage_path: str, upload_id: str,
                                  parts: list) -> dict:
        mp_dir = self._multipart_dir(upload_id)
        if not os.path.exists(mp_dir):
            raise ValueError(f"无效的 upload_id: {upload_id}")

        # 核心层不变量：按编号集合验证完整性，而不是 len(parts)。
        # 重复编号会被合并两遍（内容翻倍），缺号会静默产出截断文件。
        if not parts or not isinstance(parts, list):
            raise ValueError("分片列表不能为空")
        numbers = []
        for part in parts:
            if not isinstance(part, dict):
                raise ValueError("分片条目必须是字典")
            number = self._validate_part_number(part.get("part_number"))
            numbers.append(number)
        if len(set(numbers)) != len(numbers):
            raise ValueError("分片序号重复")
        if sorted(numbers) != list(range(1, len(numbers) + 1)):
            raise ValueError("分片序号必须从 1 连续编号，不能缺片或多片")

        # 读取目标路径
        try:
            with open(os.path.join(mp_dir, ".meta")) as f:
                target_path = f.read().strip()
        except OSError:
            target_path = storage_path

        full_path = self._full_path(target_path)
        self._ensure_parent(full_path)

        # 合并分片（先写临时文件，全部成功后原子替换）
        total_size = 0
        md5 = hashlib.md5()
        tmp_path = f"{full_path}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        try:
            with open(tmp_path, "wb") as out:
                for part in sorted(parts, key=lambda p: p["part_number"]):
                    part_path = os.path.join(mp_dir, f"part_{part['part_number']:05d}")
                    if not os.path.exists(part_path):
                        raise ValueError(f"分片 {part['part_number']} 不存在")
                    expected_etag = part.get("etag")
                    if expected_etag:
                        with open(part_path, "rb") as pf:
                            actual_etag = hashlib.md5(pf.read()).hexdigest()
                        if actual_etag != expected_etag:
                            raise ValueError(f"分片 {part['part_number']} 校验失败")
                    with open(part_path, "rb") as pf:
                        while True:
                            chunk = pf.read(8192)
                            if not chunk:
                                break
                            out.write(chunk)
                            md5.update(chunk)
                            total_size += len(chunk)
            os.replace(tmp_path, full_path)
        except BaseException:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise

        # 清理临时分片
        shutil.rmtree(mp_dir, ignore_errors=True)

        return {
            "path": target_path,
            "size": total_size,
            "etag": md5.hexdigest(),
        }

    def abort_multipart_upload(self, storage_path: str, upload_id: str) -> bool:
        mp_dir = self._multipart_dir(upload_id)
        if os.path.exists(mp_dir):
            shutil.rmtree(mp_dir, ignore_errors=True)
        return True
