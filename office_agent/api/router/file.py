"""
文件路由 - 上传/下载/管理

使用 StorageService 统一处理文件存储。
支持：本地文件系统
"""
import io
import json
from fastapi import APIRouter, UploadFile, File, HTTPException, Query
from fastapi.responses import StreamingResponse, Response

from ..schemas.response import (
    FileUploadResponse, FileInfo, BaseResponse,
)
from ..schemas.request import MultipartCompleteRequest
from ...storage import get_storage_service, FileValidationError

router = APIRouter(prefix="/api/file", tags=["文件"])

# 延迟初始化 StorageService
_storage = None


def _get_storage():
    global _storage
    if _storage is None:
        _storage = get_storage_service()
    return _storage


@router.post("/upload", response_model=BaseResponse[FileUploadResponse],
             summary="上传文件")
async def upload_file(file: UploadFile = File(...)):
    """
    上传文件

    支持：docx/pptx/xlsx/pdf/txt/md/csv/json/png/jpg 等
    流程：校验 → 存储 → 写数据库 → 返回 file_id
    """
    try:
        content = await file.read()
        storage = _get_storage()
        info = storage.upload(
            filename=file.filename or "unknown",
            content=content,
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {e}")


@router.post("/upload/multipart/init", summary="初始化分片上传")
async def init_multipart(filename: str, content_type: str = None):
    """
    初始化大文件分片上传

    返回 upload_id，后续用 upload_id 上传分片。
    """
    try:
        storage = _get_storage()
        result = storage.init_multipart_upload(filename, content_type=content_type)
        return BaseResponse(data=result)
    except FileValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upload/multipart/{file_id}/part", summary="上传分片")
async def upload_part(file_id: str, upload_id: str, part_number: int,
                      file: UploadFile = File(...)):
    """
    上传单个分片

    - **part_number**: 分片序号，从1开始
    """
    try:
        content = await file.read()
        storage = _get_storage()
        result = storage.upload_part(file_id, upload_id, part_number, content)
        return BaseResponse(data=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分片上传失败: {e}")


@router.post("/upload/multipart/{file_id}/complete", summary="完成分片上传")
async def complete_multipart(file_id: str, upload_id: str,
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"合并失败: {e}")


@router.delete("/upload/multipart/{file_id}", summary="取消分片上传")
async def abort_multipart(file_id: str, upload_id: str):
    """取消分片上传，清理已上传分片"""
    storage = _get_storage()
    storage.abort_multipart_upload(file_id, upload_id)
    return BaseResponse(message="已取消")


@router.get("/{file_id}", response_model=BaseResponse[FileInfo],
            summary="获取文件信息")
async def get_file_info(file_id: str):
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
async def download_file(file_id: str):
    """
    下载文件

    流程：数据库查询 → Storage 读取 → 返回文件流
    """
    try:
        storage = _get_storage()
        content, info = storage.download(file_id)
        return StreamingResponse(
            io.BytesIO(content),
            media_type=info.mime_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{info.original_name}"',
                "Content-Length": str(info.size),
            },
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_id}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下载失败: {e}")


@router.get("/{file_id}/versions", summary="获取文件版本列表")
async def list_versions(file_id: str):
    """获取文件的所有历史版本"""
    try:
        storage = _get_storage()
        versions = storage.get_versions(file_id)
        return BaseResponse(data={"versions": versions, "count": len(versions)})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_id}")


@router.post("/{file_id}/versions/{version_number}/restore",
             summary="恢复到指定版本")
async def restore_version(file_id: str, version_number: int):
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


@router.delete("/{file_id}", response_model=BaseResponse, summary="删除文件")
async def delete_file(file_id: str, permanent: bool = Query(default=False)):
    """
    删除文件

    - **permanent**: false=软删除（可恢复），true=物理删除
    """
    try:
        storage = _get_storage()
        storage.delete(file_id, permanent=permanent)
        return BaseResponse(message="删除成功" if permanent else "已移至回收站")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_id}")


@router.get("/", response_model=BaseResponse, summary="文件列表")
async def list_files(file_type: str = None, owner_id: str = None,
                     page: int = 1, page_size: int = 50):
    """列出文件，支持按类型筛选"""
    storage = _get_storage()
    files = storage.list_files(
        owner_id=owner_id, file_type=file_type,
        offset=(page - 1) * page_size, limit=page_size,
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
        "total": len(files),
        "page": page,
        "page_size": page_size,
    })


@router.get("/stats/overview", summary="存储统计")
async def storage_stats():
    """获取存储统计信息"""
    storage = _get_storage()
    return BaseResponse(data=storage.get_storage_stats())


@router.post("/cleanup", summary="清理临时文件")
async def cleanup_temp(hours: int = 24):
    """清理超过指定时间的临时文件"""
    storage = _get_storage()
    result = storage.cleanup_temp_files(hours)
    return BaseResponse(data=result, message=f"清理了 {result['cleaned']} 个文件")
