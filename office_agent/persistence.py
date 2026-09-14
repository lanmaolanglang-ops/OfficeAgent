"""
全项目共用的原子持久化工具。

此前 ``knowledge_base``、``security.auth.jwt``、``model_gateway.model_manager``、
``api.core.file_manager`` 各自手写了一遍「临时文件 + fsync + replace」，
口径不一（有的忘记 chmod、有的临时文件不在同目录、有的清理逻辑不一致）。
这里收敛为唯一实现，供所有关键写入复用。

语义约定：
    - 临时文件创建在目标文件所在目录，保证 ``os.replace`` 是同卷原子操作；
    - 写入后 ``flush`` + ``fsync``，尽最大努力落盘后再替换；
    - 任何异常都不会留下半截文件：临时文件被清理，原文件保持替换前内容；
    - 同一目标路径的写入在进程内串行化，避免并发写互相覆盖。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import weakref
from pathlib import Path
from typing import Any, Callable, IO, Union

logger = logging.getLogger("office_agent.persistence")

PathLike = Union[str, "os.PathLike[str]"]

# 每个目标路径一把可重入锁：同一进程内并发写同一文件必须串行，
# 否则两个线程会各自写完再 replace，后替换者覆盖先替换者。
# WeakValueDictionary: a lock is kept alive only while a writer holds a
# local reference to it; once every concurrent write to that path finishes
# the entry is collected, so writing to arbitrarily many distinct paths can
# no longer grow an unbounded dict (P3-1).
_path_locks: "weakref.WeakValueDictionary[str, threading.RLock]" = (
    weakref.WeakValueDictionary()
)
_path_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    """返回与规范化绝对路径绑定的可重入锁。

    key 经 ``resolve + normcase`` 规范化：`.`/`..` 段、盘符与路径大小写、
    斜杠方向、相对/绝对写法，以及（可解析时的）junction/symlink，指向
    同一真实文件时得到同一把锁；不同真实文件不会被误合并。路径不存在
    时 ``resolve`` 不抛错（strict=False），仍按规范化形式合并等价写法。
    """
    try:
        key = os.path.normcase(str(Path(path).resolve()))
    except OSError:
        # 解析失败（网络盘/权限等异常环境）：退回绝对路径规范化，
        # 尽力合并等价写法，保证锁仍然存在。
        key = os.path.normcase(os.path.abspath(str(path)))
    lock = _path_locks.get(key)
    if lock is None:
        with _path_locks_guard:
            lock = _path_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                _path_locks[key] = lock
    return lock


def atomic_write(path: PathLike, writer: Callable[[IO[Any]], Any], *,
                 binary: bool = False, encoding: str = "utf-8",
                 mode: int | None = None) -> None:
    """
    以「临时文件 + fsync + 原子替换」写入 ``path``。

    Args:
        path: 目标文件路径；父目录不存在时自动创建。
        writer: 接收已打开的文件句柄，负责写入内容（文本或字节）。
        binary: True 时以 ``wb`` 打开，否则以 ``w`` + ``encoding`` 打开。
        encoding: 文本模式编码。
        mode: 替换前收紧临时文件权限（例如 ``0o600``）；跨平台不支持时忽略。

    Raises:
        原始异常：写入失败时向上传播，调用方据此决定回滚内存状态。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with _lock_for(target):
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        replaced = False
        try:
            if binary:
                with os.fdopen(fd, "wb") as handle:
                    writer(handle)
                    handle.flush()
                    os.fsync(handle.fileno())
            else:
                with os.fdopen(fd, "w", encoding=encoding) as handle:
                    writer(handle)
                    handle.flush()
                    os.fsync(handle.fileno())
            if mode is not None:
                try:
                    os.chmod(temp_path, mode)
                except OSError:
                    logger.warning("无法收紧文件权限 %s: %s", temp_path, mode)
            os.replace(temp_path, target)
            replaced = True
        finally:
            if not replaced:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass


def atomic_write_bytes(path: PathLike, data: bytes, *,
                       mode: int | None = None) -> None:
    """原子写入字节内容。"""
    atomic_write(path, lambda handle: handle.write(data), binary=True, mode=mode)


def atomic_write_text(path: PathLike, text: str, *, encoding: str = "utf-8",
                      mode: int | None = None) -> None:
    """原子写入文本内容。"""
    atomic_write(path, lambda handle: handle.write(text),
                 encoding=encoding, mode=mode)


def atomic_write_json(path: PathLike, data: Any, *, indent: int | None = None,
                      ensure_ascii: bool = False, mode: int | None = None,
                      default: Callable[[Any], Any] | None = None) -> None:
    """原子写入 JSON。序列化在锁外完成，减少持锁时间。"""

    # Serialize *before* taking the per-path lock (P3-2): the critical
    # section then only covers file I/O, and we render a consistent snapshot.
    payload = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent,
                         default=default)
    atomic_write_text(path, payload, mode=mode)
