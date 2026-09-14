"""RC 回归：无 LLM 时 PPT 模板回退必须把用户真实主题放上封面，
且从长指令提取的标题应简洁（不把分页说明/页数/PPT 后缀带进成品）。"""
from office_agent.ppt_agent.content_planner import ContentPlanner


class TestFallbackCoverShowsUserTopic:
    def test_named_template_cover_uses_user_topic_not_hardcoded(self):
        planner = ContentPlanner()  # 无 model_gateway → 走模板回退
        outline = planner.plan_from_theme("Q3 销售项目汇报", slide_count=6)
        assert outline.used_template is True
        cover = outline.slides[0]
        assert cover.layout == "cover"
        assert cover.title == "Q3 销售项目汇报"
        assert cover.title != "项目汇报"  # 不得是硬编码模板名

    def test_long_instruction_cover_is_concise_topic(self):
        planner = ContentPlanner()
        raw = "生成一份 8 页的 AI 行业介绍 PPT：封面、目录、行业背景、应用场景"
        outline = planner.plan_from_theme(raw, slide_count=8)
        cover = outline.slides[0]
        assert cover.title == "AI 行业介绍"
        assert "：" not in cover.title and "页" not in cover.title
        # 回退仍产出多页骨架，不伪造空白
        assert len(outline.slides) >= 5

    def test_extract_title_variants(self):
        planner = ContentPlanner()
        assert planner._extract_title("生成一份 8 页的 AI 行业介绍 PPT：后续说明") == "AI 行业介绍"
        assert planner._extract_title("关于新产品发布") == "新产品发布"
        assert planner._extract_title("做一个年度工作总结PPT") == "年度工作总结"
        assert planner._extract_title("Q3 销售项目汇报") == "Q3 销售项目汇报"

    def test_generic_template_cover_also_uses_topic(self):
        planner = ContentPlanner()
        outline = planner.plan_from_theme("量子计算前沿综述", slide_count=5)
        assert outline.slides[0].title == "量子计算前沿综述"
