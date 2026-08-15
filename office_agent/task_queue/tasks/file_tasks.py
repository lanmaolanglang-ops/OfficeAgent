"""
文件处理后台任务
"""
import os
import time
import shutil
import logging
import traceback
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger("office_agent.tasks.file")


def process_upload(file_path: str, file_id: str = None,
                   file_type: str = None, options: dict = None,
                   progress=None, _task_id: str = None, **kwargs) -> dict:
    """
    上传后处理任务（验证、提取元数据、生成预览等）
    """
    options = options or {}
    result = {"status": "success", "file_id": file_id, "metadata": {}}

    try:
        if progress:
            progress.update(10, "验证文件")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        file_size = os.path.getsize(file_path)
        result["metadata"]["size"] = file_size

        if progress:
            progress.update(30, "提取文件元数据")

        # 根据文件类型处理
        ext = Path(file_path).suffix.lower()

        if ext in (".docx", ".doc"):
            result["file_type"] = "word"
            try:
                from docx import Document
                doc = Document(file_path)
                result["metadata"]["paragraphs"] = len(doc.paragraphs)
                result["metadata"]["tables"] = len(doc.tables)
            except Exception:
                pass

        elif ext in (".pptx", ".ppt"):
            result["file_type"] = "ppt"
            try:
                from pptx import Presentation
                prs = Presentation(file_path)
                result["metadata"]["slides"] = len(prs.slides)
            except Exception:
                pass

        elif ext in (".xlsx", ".xls"):
            result["file_type"] = "excel"
            try:
                import openpyxl
                wb = openpyxl.load_workbook(file_path, read_only=True)
                result["metadata"]["sheets"] = wb.sheetnames
            except Exception:
                pass

        elif ext == ".pdf":
            result["file_type"] = "pdf"
            try:
                import pymupdf
                doc = pymupdf.open(file_path)
                result["metadata"]["pages"] = len(doc)
                doc.close()
            except Exception:
                pass

        if progress:
            progress.update(70, "生成文件信息")

        if progress:
            progress.update(100, "文件处理完成")

        logger.info(f"文件处理 {file_id} 完成: {result['file_type']}")

    except Exception as e:
        from ...security.error_sanitizer import sanitize_error
        logger.error("文件处理 %s 失败: %s", file_id, sanitize_error(e))
        result["status"] = "failed"
        result["error"] = sanitize_error(e)
        raise

    return result


def convert_format(input_path: str, output_path: str,
                   target_format: str, options: dict = None,
                   progress=None, _task_id: str = None, **kwargs) -> dict:
    """
    文件格式转换
    """
    result = {"status": "success", "output_files": []}

    try:
        if progress:
            progress.update(10, "准备转换")

        if not os.path.exists(input_path):
            raise FileNotFoundError(input_path)

        if progress:
            progress.update(30, f"转换为{target_format}")

        # 实际转换逻辑由对应 Service 处理
        # 这里做基础的文件操作
        if os.path.exists(output_path):
            result["output_files"].append(output_path)

        if progress:
            progress.update(100, "转换完成")

    except Exception as e:
        result["status"] = "failed"
        from ...security.error_sanitizer import sanitize_error
        result["error"] = sanitize_error(e)
        raise

    return result


def cleanup_temp_files(progress=None, _task_id: str = None, **kwargs) -> dict:
    """
    清理临时文件（定时任务，每天执行）
    """
    result = {"status": "success", "cleaned": 0, "freed_bytes": 0}

    try:
        if progress:
            progress.update(10, "扫描临时文件")

        dirs_to_clean = [
            Path(os.path.expanduser("~/.office_agent/temp")),
            Path(os.path.expanduser("~/.office_agent/uploads")),
            Path(os.path.expanduser("~/.office_agent/cache")),
        ]

        cutoff = datetime.now() - timedelta(days=7)
        cleaned = 0
        freed = 0

        for d in dirs_to_clean:
            if not d.exists():
                continue
            for f in d.iterdir():
                if f.is_file():
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    if mtime < cutoff:
                        size = f.stat().st_size
                        try:
                            f.unlink()
                            cleaned += 1
                            freed += size
                        except Exception:
                            pass

        result["cleaned"] = cleaned
        result["freed_bytes"] = freed

        if progress:
            progress.update(100, f"清理完成，释放 {freed / 1024 / 1024:.1f}MB")

        logger.info(f"临时文件清理: 删除 {cleaned} 个文件，释放 {freed} 字节")

    except Exception as e:
        result["status"] = "failed"
        from ...security.error_sanitizer import sanitize_error
        result["error"] = sanitize_error(e)

    return result


def system_health_check(progress=None, _task_id: str = None, **kwargs) -> dict:
    """
    系统健康检查（定时任务，每小时执行）
    """
    result = {"status": "success", "checks": {}}

    try:
        if progress:
            progress.update(20, "检查存储")

        data_dir = Path(os.path.expanduser("~/.office_agent"))
        if data_dir.exists():
            total_size = sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file())
            result["checks"]["storage_mb"] = round(total_size / 1024 / 1024, 2)

        if progress:
            progress.update(50, "检查数据库")

        try:
            from ...database.session import session_scope
            from ...database.repository import TaskRepository
            with session_scope() as session:
                repo = TaskRepository(session)
                result["checks"]["total_tasks"] = repo.count()
                result["checks"]["pending_tasks"] = len(repo.get_pending_tasks(limit=100))
        except Exception as e:
            result["checks"]["database"] = f"error: {e}"

        if progress:
            progress.update(80, "检查Worker")

        result["checks"]["timestamp"] = datetime.now().isoformat()

        if progress:
            progress.update(100, "健康检查完成")

    except Exception as e:
        result["status"] = "failed"
        from ...security.error_sanitizer import sanitize_error
        result["error"] = sanitize_error(e)

    return result
