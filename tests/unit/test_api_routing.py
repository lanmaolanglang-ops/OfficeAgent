"""API 意图路由单一真相源的针对性测试。

根因背景：
1. 多产品关键词同时命中时旧实现按固定 PPT→Excel→Word 顺序裁决，与用户
   意图不符。现 route_intent 按文本中"最后一次明确点名"的位置裁决。
2. 老 Office 格式（.doc/.ppt/.xls）此前不被 route_by_file_path 识别，
   导致上传老格式文件时回退到文本关键词猜测。现已纳入扩展名映射。
3. resolve_route 是 chat 层与 worker 层共享的唯一权威裁决：
   文件类型优先，消息关键词仅在同 Agent 内细化子任务。
"""
from office_agent.api.routing import resolve_route, route_by_file_path, route_intent


class TestMultiProductTieBreak:
    """清单 271：多产品同时命中时不再按固定代码顺序裁决。"""

    def test_last_mentioned_product_wins_ppt_then_excel(self):
        # "PPT" 先出现、"excel" 后点名 → 应派给 excel，而非固定顺序的 ppt
        agent, task_type, _ = route_intent("把PPT里的数据搬到excel表格里")
        assert agent == "excel_agent"
        assert task_type == "excel_analyze"

    def test_last_mentioned_product_wins_excel_then_ppt(self):
        agent, _, _ = route_intent("把excel表格的数据做成PPT")
        assert agent == "ppt_agent"

    def test_last_mentioned_product_wins_word_then_ppt(self):
        agent, _, _ = route_intent("根据word文档内容生成一份PPT")
        assert agent == "ppt_agent"

    def test_single_product_unaffected(self):
        assert route_intent("分析这个excel表格")[0] == "excel_agent"
        assert route_intent("帮我排版这个文档")[0] == "word_agent"

    def test_strong_keyword_beats_weak_keyword(self):
        # 强关键词（PPT）优先于弱关键词（排版→word）
        agent, _, _ = route_intent("帮我排版这个PPT文件")
        assert agent == "ppt_agent"


class TestFilePathRouting:
    """清单 273：老 Office 格式识别（PDF/TXT 为已知剩余项）。"""

    def test_legacy_word_formats(self):
        for path in ("a.doc", "b.docx", "c.rtf", "d.odt"):
            assert route_by_file_path(path)[0] == "word_agent", path

    def test_legacy_ppt_formats(self):
        for path in ("a.ppt", "b.pptx", "c.odp"):
            assert route_by_file_path(path)[0] == "ppt_agent", path

    def test_legacy_excel_formats(self):
        for path in ("a.xls", "b.xlsx", "c.csv", "d.ods"):
            assert route_by_file_path(path)[0] == "excel_agent", path

    def test_extension_matching_is_case_insensitive(self):
        assert route_by_file_path("报告.DOC")[0] == "word_agent"
        assert route_by_file_path("数据.XLS")[0] == "excel_agent"

    def test_pdf_and_txt_route_to_word_agent(self):
        """PDF/TXT/MD 经 services.input_conversion 转换链由 word_agent 消费。"""
        for path in ("a.pdf", "a.txt", "a.md"):
            agent, task_type, source = route_by_file_path(path)
            assert (agent, task_type, source) == (
                "word_agent", "word_process", "file_type"), path


class TestFalsePositiveGuards:
    """清单 2.1：普通文本不得被关键词子串误路由。"""

    def test_doctor_does_not_hit_word_route(self):
        # 旧 process_general 用裸 "doc" 子串匹配，"doctor" 会被误派给 Word
        assert route_intent("doctor 说这个治疗方案")[0] == "orchestrator"

    def test_plain_text_falls_back_to_orchestrator(self):
        assert route_intent("总结一下这份材料")[0] == "orchestrator"


class TestResolveRouteSingleAuthority:
    """清单 2.1：chat 层与 worker 层共享 resolve_route，同一输入不得分歧。"""

    def test_file_type_is_authoritative_over_conflicting_keywords(self):
        # "排版这个PPT"：Agent 必须取文件类型（ppt），而不是 Word 弱关键词"排版"
        agent, _, _ = resolve_route("排版这个PPT", "a.pptx")
        assert agent == "ppt_agent"

    def test_message_refines_subtask_within_same_agent(self):
        # docx + "排版成公文"：同 Agent 内由消息细化出 word_format
        agent, task_type, _ = resolve_route("排版成公文", "a.docx")
        assert (agent, task_type) == ("word_agent", "word_format")

    def test_no_file_degrades_to_intent(self):
        assert resolve_route("做个PPT") == route_intent("做个PPT")
        assert resolve_route("随便聊聊") == ("orchestrator", "general", "general")

    def test_chat_layer_and_worker_layer_cannot_diverge(self):
        """两层都必须调 resolve_route（源码接线守卫）。"""
        import inspect

        from office_agent.api.router import chat
        from office_agent.task_queue import tasks

        chat_src = inspect.getsource(chat)
        tasks_src = inspect.getsource(tasks.process_general)
        assert "resolve_route(req.message, input_paths[0])" in chat_src
        assert "resolve_route(instruction, input_path)" in tasks_src
        # worker 层不再保留自己的"文件优先"拼装逻辑
        assert "route_by_file_path" not in tasks_src
