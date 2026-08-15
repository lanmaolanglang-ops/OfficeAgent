"""Single source of truth for API task routing."""
import os

WORD = ("word", "文档", "排版", "格式", "论文", "公文", "报告", "docx", "doc")
PPT = ("ppt", "pptx", "演示", "幻灯片", "汇报", "课件")
EXCEL = ("excel", "xlsx", "xls", "表格", "数据", "分析", "图表", "统计")


def route_intent(message: str) -> tuple:
    text = (message or "").lower()
    if any(k in text for k in WORD):
        return "word_agent", "word_format", "format"
    if any(k in text for k in PPT):
        return "ppt_agent", "ppt_generate", "generate"
    if any(k in text for k in EXCEL):
        return "excel_agent", "excel_analyze", "analyze"
    return "orchestrator", "general", "general"


def route_by_file_path(path: str):
    ext = os.path.splitext(path or "")[1].lower()
    if ext in (".docx", ".doc"):
        return "word_agent", "word_process", "file_type"
    if ext in (".pptx", ".ppt"):
        return "ppt_agent", "ppt_generate", "file_type"
    if ext in (".xlsx", ".xls", ".csv"):
        return "excel_agent", "excel_analyze", "file_type"
    return None
