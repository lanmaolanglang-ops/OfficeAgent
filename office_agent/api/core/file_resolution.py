"""Shared verified Storage ID to local path resolution."""
from __future__ import annotations

import logging
import os

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def resolve_input_files(file_ids, file_repo, storage=None) -> list[str]:
    if storage is None:
        from ...storage.storage_service import get_storage_service
        storage = get_storage_service()

    input_paths = []
    for file_id in file_ids or []:
        db_file = file_repo.get_by_id(file_id)
        if not db_file or db_file.status == "deleted":
            raise HTTPException(status_code=404, detail=f"文件不存在或已删除: {file_id}")
        try:
            local_path = storage.get_file_path(file_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"文件不存在或已删除: {file_id}") from exc
        if not local_path or not os.path.isfile(local_path):
            raise HTTPException(status_code=422, detail=f"文件存储内容不可用: {file_id}")
        input_paths.append(local_path)
        logger.info("已解析任务输入文件: file_id=%s", file_id)
    return input_paths
