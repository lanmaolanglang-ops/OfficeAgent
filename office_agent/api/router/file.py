"""
文件路由 - 上传/下载/管理

使用 StorageService 统一处理文件存储。
支持：本地文件系统
"""
import re
import asyncio
from urllib.parse import quote
from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..schemas.response import (
    FileUploadResponse, FileInfo, BaseResponse,
)
from ..schemas.request import MultipartCompleteRequest
from ..core.config import settings
from ..core.pagination import page_query, page_size_query
from ...storage import get_storage_service, FileValidationError
from ...storage.validators import CHUNK_SIZE

router = APIRouter(prefix="/api/file", tags=["文件"])

# 延迟初始化 StorageService
_storage = None


async def _read_upload_limited(file: UploadFile, max_size: int) -> bytes:
    """最多读取 ``max_size`` 字节，避免无 Content-Length 请求耗尽内存。"""
    chunks = []
    total = 0
    while True:
        chunk = await file.read(min(1024 * 1024, max_size + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_size:
            raise HTTPException(status_code=413, detail="上传内容超过大小限制")
    return b"".join(chunks)


def _get_storage():
    global _storage
    if _storage is None:
        _storage = get_storage_service()
    return _storage


def _effective_owner(request: Request | None, supplied: str | None = None,
                     allow_admin_scope: bool = False) -> str | None:
    if not settings.auth_enabled:
        return supplied
    if request is None:
        raise HTTPException(status_code=401, detail="缺少已认证用户")
    user_id = getattr(request.state, "user_id", None)
    if not user_id or user_id == "anonymous":
        raise HTTPException(status_code=401, detail="缺少已认证用户")
    if allow_admin_scope and getattr(request.state, "user_role", "") == "admin":
        return supplied
    return user_id


def _content_disposition(filename: str) -> str:
    """RFC 6266/5987：ASCII 回退 + filename*（中文文件名跨浏览器可用）"""
    fallback = re.sub(r'[^A-Za-z0-9._ -]', "_", filename) or "download"
    return (
        f"attachment; filename=\"{fallback}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )


@router.post("/upload", response_model=BaseResponse[FileUploadResponse],
             summary="上传文件")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """
    上传文件

    支持：docx/pptx/xlsx/pdf/txt/md/csv/json/png/jpg 等
    流程：校验 → 存储 → 写数据库 → 返回 file_id
    """
    storage = _get_storage()
    max_file_size = storage.config.max_file_size
    # 客户端声明的大小超限 → 直接 413，不把超大请求体读进内存
    declared = request.headers.get("content-length")
    # multipart 本身含 boundary/headers；实际文件恰好等于上限时，请求体会略大。
    # 仅为 multipart 留 1MB 协议开销，真实文件大小仍由 StorageService 严格校验。
    content_type = request.headers.get("content-type", "").lower()
    declared_limit = max_file_size + (
        1024 * 1024 if content_type.startswith("multipart/form-data") else 0
    )
    if declared and declared.isdigit() and int(declared) > declared_limit:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过 {max_file_size // 1024 // 1024}MB 上限",
        )
    # StorageService 是同步实现（磁盘写入 + 安全扫描）。在 async 路由里直接
    # 调用会把整块磁盘 IO 压在事件循环上，阻塞同进程内所有请求；这里显式
    # 交给工作线程，同时保留流式读取，不把大文件整体读进内存。
    owner_id = _effective_owner(request)
    try:
        info = await asyncio.to_thread(
            storage.upload_fileobj,
            filename=file.filename or "unknown",
            fileobj=file.file,
            owner_id=owner_id,
            metadata={"content_type": file.content_type},
        )
        return BaseResponse(data=FileUploadResponse(
            file_id=info.file_id,
            filename=info.original_name,
            file_type=info.file_type,
            size=info.size,
        ))
    except FileValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="上传失败，请稍后重试")


@router.post("/upload/multipart/init", summary="初始化分片上传")
def init_multipart(request: Request, filename: str,
                   expected_size: int = Query(..., ge=1),
                   expected_parts: int = Query(..., ge=1),
                   expected_sha256: str = Query(..., min_length=64, max_length=64),
                   content_type: str | None = None):
    """
    初始化大文件分片上传

    返回 upload_id，后续用 upload_id 上传分片。
    """
    return _init_multipart_impl(
        filename, expected_size, expected_parts, expected_sha256,
        content_type, request,
    )


def _init_multipart_impl(filename: str, expected_size: int, expected_parts: int,
                         expected_sha256: str, content_type: str | None = None,
                         request: Request | None = None):
    """``init_multipart`` 的内部实现，允许直接 Python 调用时省略 request。"""
    try:
        storage = _get_storage()
        result = storage.init_multipart_upload(
            filename,
            owner_id=_effective_owner(request),
            content_type=content_type,
            expected_size=expected_size,
            expected_parts=expected_parts,
            expected_sha256=expected_sha256,
        )
        return BaseResponse(data=result)
    except FileValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upload/multipart/{file_id}/part", summary="上传分片")
async def upload_part(file_id: str, upload_id: str, part_number: int,
                      file: UploadFile = File(...)):
    """
    上传单个分片

    - **part_number**: 分片序号，从1开始
    """
    try:
        content = await _read_upload_limited(file, CHUNK_SIZE)
        storage = _get_storage()
        # 分片落盘同样是同步 IO，不能直接在事件循环里执行
        result = await asyncio.to_thread(
            storage.upload_part, file_id, upload_id, part_number, content
        )
        return BaseResponse(data=result)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        # 不把内部异常字符串直接透给客户端
        raise HTTPException(status_code=500, detail="分片上传失败，请稍后重试")


@router.post("/upload/multipart/{file_id}/complete", summary="完成分片上传")
def complete_multipart(file_id: str, upload_id: str,
                              body: MultipartCompleteRequest):
    """
    完成分片上传，合并所有分片

    - **parts**: [{"part_number": 1, "etag": "..."}, ...]
    """
    try:
        storage = _get_storage()
        parts = [{"part_number": p.part_number, "etag": p.etag} for p in body.parts]
        info = storage.complete_multipart_upload(file_id, upload_id, parts)
        return BaseResponse(data=FileUploadResponse(
            file_id=info.file_id,
            filename=info.original_name,
            file_type=info.file_type,
            size=info.size,
        ))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="分片合并失败，请稍后重试")


@router.delete("/upload/multipart/{file_id}", summary="取消分片上传")
def abort_multipart(file_id: str, upload_id: str):
    """取消分片上传，清理已上传分片"""
    storage = _get_storage()
    storage.abort_multipart_upload(file_id, upload_id)
    return BaseResponse(message="已取消")


@router.get("/trash", response_model=BaseResponse, summary="回收站文件列表")
def list_deleted_files(request: Request, file_type: str | None = None,
                       owner_id: str | None = None,
                       page: int = page_query(),
                       page_size: int = page_size_query(default=50)):
    """列出可恢复的软删除文件，不包含正在永久删除的 tombstone。"""
    return _list_deleted_files_impl(file_type, owner_id, page, page_size, request)


def _list_deleted_files_impl(file_type: str | None, owner_id: str | None,
                             page: int, page_size: int,
                             request: Request | None = None):
    """``list_deleted_files`` 的内部实现，允许直接 Python 调用时省略 request。"""
    storage = _get_storage()
    files = storage.list_deleted_files(
        owner_id=_effective_owner(request, owner_id, allow_admin_scope=True),
        file_type=file_type,
        offset=(page - 1) * page_size, limit=page_size,
    )
    total = storage.count_deleted_files(
        owner_id=_effective_owner(request, owner_id, allow_admin_scope=True),
        file_type=file_type
    )
    return BaseResponse(data={
        "files": [{
            "file_id": f.file_id,
            "filename": f.original_name,
            "original_name": f.original_name,
            "file_type": f.file_type,
            "extension": f.extension,
            "size": f.size,
            "version": f.version,
            "status": f.status,
            "upload_time": f.created_at.isoformat() if f.created_at else "",
            "deleted_at": f.deleted_at.isoformat() if f.deleted_at else None,
        } for f in files],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.get("/{file_id}", response_model=BaseResponse[FileInfo],
            summary="获取文件信息")
def get_file_info(file_id: str):
    """查询文件元数据"""
    try:
        storage = _get_storage()
        info = storage.get_info(file_id)
        return BaseResponse(data=FileInfo(
            file_id=info.file_id,
            filename=info.original_name,
            original_name=info.original_name,
            file_type=info.file_type,
            extension=info.extension,
            size=info.size,
            upload_time=info.created_at.isoformat() if info.created_at else "",
            version=info.version,
            status=info.status,
            metadata=info.metadata,
        ))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_id}")


@router.get("/download/{file_id}", summary="下载文件")
def download_file(file_id: str):
    """
    下载文件

    流程：数据库查询 → Storage 分块读取 → 流式返回（不整读进内存）
    """
    try:
        storage = _get_storage()
        chunks, info = storage.stream_download(file_id)
        return StreamingResponse(
            chunks,
            media_type=info.mime_type or "application/octet-stream",
            headers={
                "Content-Disposition": _content_disposition(info.original_name or "download"),
                "Content-Length": str(info.size or 0),
            },
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_id}")
    except Exception:
        raise HTTPException(status_code=500, detail="下载失败，请稍后重试")


@router.get("/{file_id}/versions", summary="获取文件版本列表")
def list_versions(file_id: str):
    """获取文件的所有历史版本"""
    try:
        storage = _get_storage()
        versions = storage.get_versions(file_id)
        # storage_path 是本机内部路径，不应通过 API 暴露。
        safe_versions = [
            {key: value for key, value in version.items() if key != "storage_path"}
            for version in versions
        ]
        return BaseResponse(data={"versions": safe_versions, "count": len(safe_versions)})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_id}")


@router.post("/{file_id}/versions/{version_number}/restore",
             summary="恢复到指定版本")
def restore_version(file_id: str, version_number: int):
    """将文件恢复到指定历史版本"""
    try:
        storage = _get_storage()
        info = storage.restore_version(file_id, version_number)
        return BaseResponse(data=FileInfo(
            file_id=info.file_id,
            filename=info.original_name,
            original_name=info.original_name,
            file_type=info.file_type,
            size=info.size,
            version=info.version,
        ), message=f"已恢复到版本 {version_number}")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{file_id}/restore", response_model=BaseResponse,
             summary="从回收站恢复文件")
def restore_deleted_file(file_id: str):
    """恢复软删除文件。物理内容缺失时保持删除状态并返回可操作错误。"""
    try:
        info = _get_storage().restore_deleted(file_id)
        return BaseResponse(data=FileInfo(
            file_id=info.file_id,
            filename=info.original_name,
            original_name=info.original_name,
            file_type=info.file_type,
            extension=info.extension,
            size=info.size,
            upload_time=info.created_at.isoformat() if info.created_at else "",
            version=info.version,
            status=info.status,
        ), message="文件已恢复")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="回收站中不存在该文件")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/{file_id}", response_model=BaseResponse, summary="删除文件")
def delete_file(file_id: str, permanent: bool = Query(default=False)):
    """
    删除文件

    - **permanent**: false=软删除（可恢复），true=物理删除
    """
    try:
        storage = _get_storage()
        deleted = storage.delete(file_id, permanent=permanent)
        if not deleted:
            raise FileNotFoundError(file_id)
        return BaseResponse(message="删除成功" if permanent else "已移至回收站")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_id}")


@router.get("/", response_model=BaseResponse, summary="文件列表")
def list_files(request: Request, file_type: str | None = None,
               owner_id: str | None = None,
               page: int = page_query(),
               page_size: int = page_size_query(default=50)):
    """列出文件，支持按类型筛选"""
    return _list_files_impl(file_type, owner_id, page, page_size, request)


def _list_files_impl(file_type: str | None, owner_id: str | None,
                     page: int, page_size: int,
                     request: Request | None = None):
    """``list_files`` 的内部实现，允许直接 Python 调用时省略 request。"""
    storage = _get_storage()
    files = storage.list_files(
        owner_id=_effective_owner(request, owner_id, allow_admin_scope=True),
        file_type=file_type,
        offset=(page - 1) * page_size, limit=page_size,
    )
    total = storage.count_files(
        owner_id=_effective_owner(request, owner_id, allow_admin_scope=True),
        file_type=file_type
    )
    return BaseResponse(data={
        "files": [{
            "file_id": f.file_id,
            "filename": f.original_name,
            "original_name": f.original_name,
            "file_type": f.file_type,
            "extension": f.extension,
            "size": f.size,
            "version": f.version,
            "status": f.status,
            "upload_time": f.created_at.isoformat() if f.created_at else "",
        } for f in files],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.get("/stats/overview", summary="存储统计")
def storage_stats():
    """获取存储统计信息"""
    storage = _get_storage()
    return BaseResponse(data=storage.get_storage_stats())


@router.post("/cleanup", summary="清理临时文件")
async def cleanup_temp(hours: int = Query(default=24, ge=1, le=24 * 30)):
    """清理超过指定时间的临时文件（hours 限制在 [1, 720]，防止误删全部）"""
    storage = _get_storage()
    result = await asyncio.to_thread(storage.cleanup_temp_files, hours)
    cleaned = result.get("cleaned", 0) if isinstance(result, dict) else 0
    return BaseResponse(data=result, message=f"清理了 {cleaned} 个文件")
