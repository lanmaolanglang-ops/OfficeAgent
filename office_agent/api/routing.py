"""Single source of truth for API task routing."""
import os

# 强关键词：明确点名了目标产品，优先级高于弱关键词。
# 避免“做一份季度总结报告PPT”因命中“报告”被误派给 Word。
WORD_STRONG = ("word", "docx", "doc文件", "文档", "论文", "公文")
PPT_STRONG = ("ppt", "pptx", "幻灯片", "演示文稿", "课件", "slides")
EXCEL_STRONG = ("excel", "xlsx", "xls", "表格", "电子表格", "csv")

# 弱关键词：仅在没有强关键词时作为兜底信号（保持旧版优先级 WORD→PPT→EXCEL）
WORD_WEAK = ("排版", "格式")
PPT_WEAK = ("演示", "汇报")
EXCEL_WEAK = ("数据", "分析", "图表", "统计")


def route_intent(message: str) -> tuple:
    text = (message or "").lower()
    routes = (
        (WORD_STRONG, ("word_agent", "word_format", "format")),
        (PPT_STRONG, ("ppt_agent", "ppt_generate", "generate")),
        (EXCEL_STRONG, ("excel_agent", "excel_analyze", "analyze")),
    )
    # 多产品同时出现时按用户文本中最后一次明确点名的目标裁决，
    # 而不是依赖固定的 PPT→Excel→Word 代码顺序。
    strong_matches = []
    for keywords, route in routes:
        positions = [text.rfind(keyword) for keyword in keywords if keyword in text]
        if positions:
            strong_matches.append((max(positions), route))
    if strong_matches:
        return max(strong_matches, key=lambda item: item[0])[1]
    # 弱关键词兜底
    if any(k in text for k in WORD_WEAK):
        return "word_agent", "word_format", "format"
    if any(k in text for k in PPT_WEAK):
        return "ppt_agent", "ppt_generate", "generate"
    if any(k in text for k in EXCEL_WEAK):
        return "excel_agent", "excel_analyze", "analyze"
    return "orchestrator", "general", "general"


def route_by_file_path(path: str):
    ext = os.path.splitext(path or "")[1].lower()
    if ext in (".docx", ".doc", ".rtf", ".odt"):
        return "word_agent", "word_process", "file_type"
    if ext in (".pptx", ".ppt", ".odp"):
        return "ppt_agent", "ppt_generate", "file_type"
    if ext in (".xlsx", ".xls", ".csv", ".ods"):
        return "excel_agent", "excel_analyze", "file_type"
    return None
