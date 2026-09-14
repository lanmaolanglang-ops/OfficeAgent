"""
File Security Manager - 文件安全管理
用户文件隔离、访问控制、安全路径
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from dataclasses import dataclass

from .file_scanner import FileScanner, ScanResult

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

    def __init__(self, storage_root: str | Path | None = None,
                 scanner: FileScanner | None = None):
        if storage_root is None:
            # P3-54: 默认落到平台数据根，而不是随进程 CWD 漂移的相对路径
            from ...runtime_config import get_data_root
            storage_root = get_data_root() / "storage" / "users"
        self.storage_root = Path(storage_root)
        self.scanner = scanner or FileScanner()
        self._spaces: dict[str, UserFileSpace] = {}

    @staticmethod
    def _safe_user_id(user_id: str) -> str:
        """user_id 直接拼进路径，必须拒绝路径分隔符与穿越段。"""
        value = str(user_id or "").strip()
        if not value or value in {".", ".."}:
            raise ValueError("无效的用户标识")
        if any(ch in value for ch in ("/", "\\", "\0")):
            raise ValueError("用户标识包含非法路径字符")
        if ":" in value:  # Windows drive / ADS
            raise ValueError("用户标识包含非法路径字符")
        if len(value) > 64 or not all(c.isalnum() or c in "._-@" for c in value):
            raise ValueError("用户标识包含非法字符")
        return value

    def get_user_space(self, user_id: str) -> UserFileSpace:
        """获取用户文件空间"""
        safe_id = self._safe_user_id(user_id)
        if safe_id not in self._spaces:
            root = (self.storage_root / safe_id).resolve()
            # 防御：即使 storage_root 本身被 symlink 改写，也必须落在 root 之下
            try:
                root.relative_to(self.storage_root.resolve())
            except ValueError:
                raise PermissionError("用户存储根越界")
            space = UserFileSpace(
                user_id=safe_id,
                root=root,
                uploads=root / "uploads",
                outputs=root / "outputs",
                temp=root / "temp",
            )
            space.ensure()
            self._spaces[safe_id] = space
        return self._spaces[safe_id]

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
        # P3-60: 危险可执行扩展名必须中和——即使文件名本身不含穿越字符
        # （is_filename_safe 只查路径遍历，查不出 .exe），也不能原样落盘。
        ext = Path(filename).suffix
        blocked = {e.lower() for e in self.scanner.blocked_extensions}
        dangerous_ext = ext.lower() in blocked
        if dangerous_ext:
            ext = ".bin"
        if dangerous_ext or not self.scanner.is_filename_safe(filename):
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
