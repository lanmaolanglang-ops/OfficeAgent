"""弱关键词误路由治理专项测试（清单 324）。

修复策略（不重写 resolve_route 架构、不引入 LLM、不扩充关键词表）：
1. 边界匹配：ASCII 关键词按词边界匹配——"keyword" 不再误中 "word"、
   "excellent" 不再误中 "excel"；中文关键词保持子串匹配。
2. 强弱分级：维持既有"强关键词优先、弱关键词兜底"结构不变。
3. 上下文约束（仅弱关键词）：口语化"X一下"动词用法（演示一下/汇报一下/
   分析一下/统计一下）与技术复合词内部命中（数据结构/数据库里的"数据"）
   不构成办公产品意图。
"""
from office_agent.api.routing import resolve_route, route_intent


class TestAsciiBoundaryMatching:
    """ASCII 关键词不得作为更长单词的子串误命中。"""

    def test_keyword_does_not_hit_word(self):
        assert route_intent("keyword 密度对 SEO 的影响")[0] == "orchestrator"

    def test_excellent_does_not_hit_excel(self):
        assert route_intent("excellent 和 good 的区别")[0] == "orchestrator"

    def test_password_does_not_hit_word(self):
        assert route_intent("password 怎么重置")[0] == "orchestrator"

    def test_ascii_keyword_still_matches_at_boundaries(self):
        assert route_intent("做个PPT")[0] == "ppt_agent"
        assert route_intent("分析这个excel表格")[0] == "excel_agent"
        assert route_intent("把这份word文档重新排版")[0] == "word_agent"

    def test_doctor_case_still_safe(self):
        # 历史误路由案例（裸 "doc" 子串时代）保持正确
        assert route_intent("doctor 说这个治疗方案")[0] == "orchestrator"


class TestWeakKeywordContextConstraints:
    """弱关键词的自然语言误命中必须被上下文约束排除。"""

    def test_casual_verb_yixia_not_ppt(self):
        # "演示一下"是口语动词（demo），不是"做演示文稿"
        assert route_intent("演示一下怎么部署服务")[0] == "orchestrator"

    def test_casual_report_yixia_not_ppt(self):
        # "汇报一下进度"是日常汇报动作，不是"做汇报 PPT"
        assert route_intent("汇报一下今天的工作进度")[0] == "orchestrator"

    def test_casual_analyze_yixia_not_excel(self):
        # "分析一下代码报错"不是表格分析
        assert route_intent("分析一下这段代码为什么报错")[0] == "orchestrator"

    def test_casual_count_yixia_not_excel(self):
        assert route_intent("统计一下这个项目有多少文件")[0] == "orchestrator"

    def test_data_structure_compound_not_excel(self):
        assert route_intent("数据结构的常用实现方式有哪些")[0] == "orchestrator"

    def test_database_compound_not_excel(self):
        assert route_intent("这个数据库查询很慢")[0] == "orchestrator"


class TestWeakKeywordLegitimateUsePreserved:
    """弱关键词的正当用法不得被上下文约束误杀。"""

    def test_format_adjustment_still_word(self):
        assert route_intent("这个报告的格式需要调整")[0] == "word_agent"

    def test_typesetting_still_word(self):
        assert route_intent("帮我排版一下")[0] == "word_agent"

    def test_report_noun_still_ppt(self):
        # "季度汇报"作名词（汇报材料），非"汇报一下"口语动词
        assert route_intent("帮我做一份季度汇报")[0] == "ppt_agent"

    def test_data_chart_still_excel(self):
        assert route_intent("把数据整理成图表")[0] == "excel_agent"

    def test_yixia_after_other_word_unaffected(self):
        # "一下"紧跟的是"整理"而非弱关键词"数据"，约束不触发
        assert route_intent("把数据整理一下")[0] == "excel_agent"


class TestStrongKeywordPrecedenceUnchanged:
    """强关键词裁决与既有测试契约保持不变。"""

    def test_strong_beats_weak(self):
        assert route_intent("帮我排版这个PPT文件")[0] == "ppt_agent"

    def test_last_mentioned_wins(self):
        assert route_intent("把PPT里的数据搬到excel表格里")[0] == "excel_agent"
        assert route_intent("根据word文档内容生成一份PPT")[0] == "ppt_agent"

    def test_file_type_still_authoritative(self):
        # 文件类型权威不因关键词约束改变
        assert resolve_route("演示一下这个文件", "a.pptx")[0] == "ppt_agent"
        assert resolve_route("分析一下", "b.xlsx")[0] == "excel_agent"

    def test_no_file_degrades_to_intent(self):
        assert resolve_route("随便聊聊") == ("orchestrator", "general", "general")
