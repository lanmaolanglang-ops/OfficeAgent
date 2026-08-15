"""
File Security Manager - 文件安全管理
用户文件隔离、访问控制、安全路径
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from .file_scanner import FileScanner, ScanResult, ThreatLevel

try:
    from office_agent.logging_system import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


@dataclass
class UserFileSpace:
    """用户文件空间"""
    user_id: str
    root: Path
    uploads: Path
    outputs: Path
    temp: Path

    def ensure(self):
        """确保目录存在"""
        for d in (self.root, self.uploads, self.outputs, self.temp):
            d.mkdir(parents=True, exist_ok=True)


class FileSecurityManager:
    """
    文件安全管理器

    功能：
    1. 用户文件空间隔离（storage/users/{user_id}/{uploads,outputs,temp}）
    2. 路径遍历防护
    3. 文件上传扫描
    4. 文件访问权限检查
    """

    def __init__(self, storage_root: str | Path = "./storage/users",
                 scanner: FileScanner | None = None):
        self.storage_root = Path(storage_root)
        self.scanner = scanner or FileScanner()
        self._spaces: dict[str, UserFileSpace] = {}

    def get_user_space(self, user_id: str) -> UserFileSpace:
        """获取用户文件空间"""
        if user_id not in self._spaces:
            root = self.storage_root / user_id
            space = UserFileSpace(
                user_id=user_id,
                root=root,
                uploads=root / "uploads",
                outputs=root / "outputs",
                temp=root / "temp",
            )
            space.ensure()
            self._spaces[user_id] = space
        return self._spaces[user_id]

    def safe_path(self, user_id: str, category: str, filename: str) -> Path:
        """
        获取安全的文件路径
        category: uploads | outputs | temp
        """
        # 验证文件名
        if not self.scanner.is_filename_safe(filename):
            raise ValueError(f"不安全的文件名: {filename}")
        space = self.get_user_space(user_id)
        base = getattr(space, category, None)
        if base is None:
            raise ValueError(f"无效的文件分类: {category}")
        return base / filename

    def resolve_user_path(self, user_id: str, filepath: str | Path) -> Path:
        """
        解析用户文件路径，确保在用户空间内
        防止路径遍历攻击
        """
        space = self.get_user_space(user_id)
        # 解析真实路径
        target = (space.root / filepath).resolve()
        root = space.root.resolve()
        # 确保目标在用户空间内
        try:
            target.relative_to(root)
        except ValueError:
            raise PermissionError(f"路径越界访问: {filepath}")
        return target

    def check_file_access(self, user_id: str, filepath: str | Path,
                          owner_id: str | None = None) -> bool:
        """
        检查用户是否有权访问文件
        用户只能访问自己空间内的文件
        """
        try:
            if owner_id and owner_id != user_id:
                return False
            self.resolve_user_path(user_id, filepath)
            return True
        except (PermissionError, ValueError):
            return False

    def scan_upload(self, file_path: str | Path, user_id: str,
                    original_filename: str | None = None) -> ScanResult:
        """扫描上传文件"""
        result = self.scanner.scan_file(file_path, original_filename)
        if result.is_blocked:
            logger.warning(f"Blocked upload for user {user_id}: {result.detected_threats}")
        return result

    def safe_upload(self, source_path: str | Path, user_id: str,
                    filename: str) -> tuple[Path, ScanResult]:
        """
        安全上传文件
        返回(目标路径, 扫描结果)
        如果文件危险，抛出异常
        """
        # 先扫描
        result = self.scanner.scan_file(source_path, filename)
        if result.is_blocked:
            raise PermissionError(f"文件被安全策略阻止: {result.detected_threats}")

        # 复制到用户空间
        dest = self.safe_path(user_id, "uploads", filename)
        shutil.copy2(source_path, dest)
        logger.info(f"File uploaded: user={user_id}, file={filename}, size={result.file_size}")
        return dest, result

    def save_output(self, source_path: str | Path, user_id: str,
                    filename: str) -> Path:
        """保存输出文件到用户空间"""
        if not self.scanner.is_filename_safe(filename):
            # 生成安全文件名
            ext = Path(filename).suffix
            filename = f"output_{uuid.uuid4().hex[:8]}{ext}"
        dest = self.safe_path(user_id, "outputs", filename)
        shutil.copy2(source_path, dest)
        return dest

    def cleanup_temp(self, user_id: str, max_age_hours: int = 24):
        """清理临时文件"""
        import time
        space = self.get_user_space(user_id)
        now = time.time()
        for f in space.temp.iterdir():
            if f.is_file() and (now - f.stat().st_mtime) > max_age_hours * 3600:
                try:
                    f.unlink()
                except Exception:
                    pass

    def get_user_file_list(self, user_id: str, category: str = "uploads") -> list[dict]:
        """列出用户文件"""
        space = self.get_user_space(user_id)
        base = getattr(space, category, space.uploads)
        files = []
        for f in base.iterdir():
            if f.is_file():
                stat = f.stat()
                files.append({
                    "name": f.name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "path": str(f),
                })
        return files

    def delete_user_file(self, user_id: str, filename: str,
                         category: str = "uploads") -> bool:
        """删除用户文件"""
        try:
            path = self.safe_path(user_id, category, filename)
            if path.exists():
                path.unlink()
                logger.info(f"File deleted: user={user_id}, file={filename}")
                return True
            return False
        except (ValueError, PermissionError) as e:
            logger.warning(f"Delete denied: user={user_id}, file={filename}: {e}")
            return False
