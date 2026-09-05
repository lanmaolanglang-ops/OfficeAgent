"""Single source of truth for API task routing."""
import os
import re

# 强关键词：明确点名了目标产品，优先级高于弱关键词。
# 避免“做一份季度总结报告PPT”因命中“报告”被误派给 Word。
WORD_STRONG = ("word", "docx", "doc文件", "文档", "论文", "公文")
PPT_STRONG = ("ppt", "pptx", "幻灯片", "演示文稿", "课件", "slides")
EXCEL_STRONG = ("excel", "xlsx", "xls", "表格", "电子表格", "csv")

# 弱关键词：仅在没有强关键词时作为兜底信号（保持旧版优先级 WORD→PPT→EXCEL）
WORD_WEAK = ("排版", "格式")
PPT_WEAK = ("演示", "汇报")
EXCEL_WEAK = ("数据", "分析", "图表", "统计")

# 弱关键词上下文约束（只作用于弱关键词，强关键词不受限）：
# 1. 口语化“X一下”用法（“演示一下怎么部署”“汇报一下进度”“分析一下报错”）
#    是随意动词，不构成办公产品意图。仅约束语义可脱离办公场景的动词；
#    “排版一下”这类本身就是办公诉求的词不受此约束。
# 2. 技术复合词内部的命中（“数据结构”“数据库”里的“数据”）不是表格信号。
_WEAK_CASUAL_SUFFIX = "一下"
_WEAK_CASUAL_VERBS = ("演示", "汇报", "分析", "统计")
_WEAK_COMPOUND_BLOCK = {
    "数据": ("数据结构", "数据库"),
}


def _keyword_positions(text: str, keyword: str) -> list:
    """返回 keyword 在 text（已 lowercase）中的所有命中位置。

    ASCII 关键词按词边界匹配，避免子串误命中：
    “keyword”不再误中“word”、“excellent”不再误中“excel”。
    中文关键词无词边界概念，保持子串匹配。
    """
    if keyword.isascii():
        return [
            m.start()
            for m in re.finditer(
                rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text
            )
        ]
    positions: list[int] = []
    start = 0
    while True:
        idx = text.find(keyword, start)
        if idx < 0:
            return positions
        positions.append(idx)
        start = idx + 1


def _weak_keyword_hit(text: str, keywords: tuple) -> bool:
    """弱关键词命中判定：任一命中不被上下文约束排除即为命中。"""
    for keyword in keywords:
        for pos in _keyword_positions(text, keyword):
            if keyword in _WEAK_CASUAL_VERBS and text.startswith(
                _WEAK_CASUAL_SUFFIX, pos + len(keyword)
            ):
                continue
            if any(
                text.startswith(compound, pos)
                for compound in _WEAK_COMPOUND_BLOCK.get(keyword, ())
            ):
                continue
            return True
    return False


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
        positions = [
            pos for keyword in keywords for pos in _keyword_positions(text, keyword)
        ]
        if positions:
            strong_matches.append((max(positions), route))
    if strong_matches:
        return max(strong_matches, key=lambda item: item[0])[1]
    # 弱关键词兜底（带上下文约束，见 _weak_keyword_hit）
    if _weak_keyword_hit(text, WORD_WEAK):
        return "word_agent", "word_format", "format"
    if _weak_keyword_hit(text, PPT_WEAK):
        return "ppt_agent", "ppt_generate", "generate"
    if _weak_keyword_hit(text, EXCEL_WEAK):
        return "excel_agent", "excel_analyze", "analyze"
    return "orchestrator", "general", "general"


def route_by_file_path(path: str):
    ext = os.path.splitext(path or "")[1].lower()
    # 文本类扩展名经 services.input_conversion 转换为 docx 后由 word_agent 消费
    if ext in (".docx", ".doc", ".rtf", ".odt", ".txt", ".md", ".pdf"):
        return "word_agent", "word_process", "file_type"
    if ext in (".pptx", ".ppt", ".odp"):
        return "ppt_agent", "ppt_generate", "file_type"
    if ext in (".xlsx", ".xls", ".csv", ".ods"):
        return "excel_agent", "excel_analyze", "file_type"
    return None


def resolve_route(message: str, input_path: str | None = None) -> tuple:
    """单一权威路由裁决：chat 层与 worker 层必须调用本函数，禁止各自拼装。

    优先级（两层历史行为已一致，此处固化为唯一实现）：
    1. 附带文件的类型是权威路由——避免“排版这个PPT”被关键词“排版”
       误派给 Word 去打开 pptx；
    2. 消息关键词仅在同 Agent 内细化子任务
       （docx + “排版成公文” → word_format 而非 word_process）；
    3. 无文件时退化为纯消息意图路由。
    """
    routed = route_by_file_path(input_path) if input_path else None
    if not routed:
        return route_intent(message)
    msg_agent, msg_task_type, msg_intent = route_intent(message)
    if msg_agent == routed[0]:
        return msg_agent, msg_task_type, msg_intent
    return routed
