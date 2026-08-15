"""
Slide Planner - 智能 PPT 结构规划器

根据主题、场景、目标人群、文档内容，自动生成完整的 PPT 结构。
输出 SlidePlan JSON，可直接用于 PPT 生成。

支持场景：
- thesis_defense    论文答辩
- business_report   商业汇报
- project_intro     项目介绍
- teaching_training 教学培训
- product_launch    产品发布
- general           通用

支持目标人群：
- expert      专业人士（技术细节、数据、术语）
- general     普通听众（通俗解释、案例、故事）
- executive   管理层（结论先行、数据、ROI）
- student     学生（基础知识、循序渐进、示例）
- customer    客户（价值主张、收益、案例）
"""
import json
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict


# ==========================================
# 场景模板定义
# ==========================================

@dataclass
class SlideTemplate:
    """单页模板"""
    layout: str               # 版式类型
    title_key: str            # 标题模板（可含 {topic} 占位符）
    bullet_suggestions: list  # 建议要点
    notes: str = ""           # 演讲备注建议
    importance: str = "normal"  # must/normal/optional
    min_chars: int = 0        # 最少内容字数
    max_bullets: int = 5      # 最多要点数


@dataclass
class SceneTemplate:
    """场景模板"""
    scene_id: str
    name: str
    description: str
    default_slides: int
    slides: List[SlideTemplate]
    style_hint: str = "professional"
    recommended_layouts: list = field(default_factory=list)


# 论文答辩
THESIS_DEFENSE = SceneTemplate(
    scene_id="thesis_defense",
    name="论文答辩",
    description="学位论文/学术研究答辩，注重研究方法、实验数据、学术规范",
    default_slides=12,
    style_hint="academic",
    slides=[
        SlideTemplate("cover", "{topic}", [],
                      "封面包含：论文题目、答辩人、导师、学校、日期", "must"),
        SlideTemplate("toc", "目录", ["研究背景与意义", "研究问题与目标", "研究方法",
                                       "实验与结果", "结论与展望"], "", "must"),
        SlideTemplate("section", "研究背景", [], "阐述研究领域和大背景", "must"),
        SlideTemplate("content", "研究背景与意义",
                      ["研究领域概述", "国内外研究现状", "研究的理论意义", "研究的实践价值"],
                      "用数据说明问题的重要性", "must"),
        SlideTemplate("content", "研究问题与目标",
                      ["核心研究问题", "研究目标", "研究假设", "创新点"],
                      "明确提出要解决什么问题", "must"),
        SlideTemplate("content", "文献综述",
                      ["相关理论基础", "已有研究成果", "研究空白", "本研究定位"],
                      "简要梳理，突出研究空白", "normal"),
        SlideTemplate("section", "研究方法", [], "", "must"),
        SlideTemplate("content", "研究方法与设计",
                      ["研究方法选择", "数据来源", "实验/调查设计", "分析方法"],
                      "方法要可复现", "must"),
        SlideTemplate("section", "实验结果", [], "", "must"),
        SlideTemplate("data_cards", "核心数据",
                      ["关键指标1", "关键指标2", "关键指标3", "关键指标4"],
                      "用数据卡片展示最核心的实验数据", "must"),
        SlideTemplate("content", "实验结果与分析",
                      ["主要发现", "结果分析", "与假设对比", "讨论"],
                      "图表结合，数据说话", "must"),
        SlideTemplate("content", "结论与展望",
                      ["主要结论", "研究贡献", "研究局限", "未来方向"],
                      "结论要呼应研究问题", "must"),
        SlideTemplate("summary", "致谢", ["感谢导师指导", "感谢评审专家", "感谢实验室同学"],
                      "简洁真诚", "must"),
    ],
)

# 商业汇报
BUSINESS_REPORT = SceneTemplate(
    scene_id="business_report",
    name="商业汇报",
    description="商业计划/融资/经营汇报，注重市场、商业模式、财务数据",
    default_slides=12,
    style_hint="professional",
    slides=[
        SlideTemplate("cover", "{topic}", [], "公司名+Logo+日期", "must"),
        SlideTemplate("toc", "目录", ["执行摘要", "市场分析", "解决方案", "商业模式",
                                       "运营数据", "团队", "融资计划"], "", "must"),
        SlideTemplate("content", "执行摘要",
                      ["一句话定位", "核心价值主张", "关键数据亮点", "融资需求"],
                      "结论先行，让听众30秒内get到重点", "must"),
        SlideTemplate("section", "市场与机会", [], "", "must"),
        SlideTemplate("content", "市场背景",
                      ["市场规模与增速", "行业趋势", "政策环境", "市场机会"],
                      "用权威数据，标注来源", "must"),
        SlideTemplate("content", "痛点分析",
                      ["用户痛点", "现有方案不足", "市场空白", "目标客群"],
                      "痛点要真实、具体", "must"),
        SlideTemplate("section", "解决方案", [], "", "must"),
        SlideTemplate("content_image", "产品/服务介绍",
                      ["核心功能", "差异化优势", "技术壁垒", "使用场景"],
                      "图文并茂，一图胜千言", "must"),
        SlideTemplate("two_column", "竞争分析",
                      ["我们的优势", "竞品劣势"],
                      "客观对比，突出差异化", "normal"),
        SlideTemplate("section", "商业模式", [], "", "must"),
        SlideTemplate("content", "商业模式",
                      ["收入来源", "成本结构", "盈利模式", "客户获取"],
                      "商业模式画布简化版", "must"),
        SlideTemplate("data_cards", "运营数据",
                      ["用户数", "收入", "增长率", "留存率"],
                      "用数据证明 traction", "must"),
        SlideTemplate("timeline", "发展规划",
                      ["近期目标", "中期规划", "长期愿景"],
                      "时间线清晰", "normal"),
        SlideTemplate("content", "团队介绍",
                      ["核心成员背景", "顾问团队", "团队优势"],
                      "突出相关经验", "normal"),
        SlideTemplate("content", "融资计划",
                      ["融资金额", "资金用途", "出让股权", "里程碑"],
                      "数字要具体", "normal"),
        SlideTemplate("summary", "感谢", ["联系方式", "Q&A"], "", "must"),
    ],
)

# 项目介绍
PROJECT_INTRO = SceneTemplate(
    scene_id="project_intro",
    name="项目介绍",
    description="项目立项/进展/总结汇报，注重目标、方案、进度、成果",
    default_slides=10,
    style_hint="professional",
    slides=[
        SlideTemplate("cover", "{topic}", [], "项目名称+汇报人+日期", "must"),
        SlideTemplate("toc", "目录", ["项目背景", "项目目标", "实施方案",
                                       "进度计划", "阶段成果", "风险与对策"], "", "must"),
        SlideTemplate("content", "项目背景",
                      ["立项背景", "业务需求", "项目意义", "预期价值"],
                      "为什么要做这个项目", "must"),
        SlideTemplate("content", "项目目标",
                      ["总体目标", "具体目标", "关键指标(KPI)", "成功标准"],
                      "SMART原则", "must"),
        SlideTemplate("section", "实施方案", [], "", "must"),
        SlideTemplate("content", "技术方案",
                      ["技术架构", "核心模块", "技术选型", "关键技术难点"],
                      "架构图很重要", "must"),
        SlideTemplate("content", "实施计划",
                      ["里程碑节点", "阶段划分", "资源分配", "时间安排"],
                      "甘特图/时间线", "must"),
        SlideTemplate("timeline", "项目里程碑",
                      ["阶段一", "阶段二", "阶段三", "阶段四"],
                      "关键节点", "must"),
        SlideTemplate("section", "成果与进展", [], "", "must"),
        SlideTemplate("data_cards", "关键数据",
                      ["完成度", "效率提升", "成本节约", "用户满意度"],
                      "量化成果", "must"),
        SlideTemplate("content", "阶段成果",
                      ["已完成工作", "核心产出", "价值体现", "亮点展示"],
                      "用事实和数据说话", "must"),
        SlideTemplate("two_column", "风险与对策",
                      ["主要风险", "应对措施"],
                      "主动暴露风险并给出方案", "normal"),
        SlideTemplate("content", "下一步计划",
                      ["后续工作", "资源需求", "时间节点", "预期目标"],
                      "", "normal"),
        SlideTemplate("summary", "总结", ["核心要点回顾", "感谢支持", "Q&A"], "", "must"),
    ],
)

# 教学培训
TEACHING_TRAINING = SceneTemplate(
    scene_id="teaching_training",
    name="教学培训",
    description="课程教学/技能培训，注重知识体系、循序渐进、案例实践",
    default_slides=12,
    style_hint="academic",
    slides=[
        SlideTemplate("cover", "{topic}", [], "课程名+讲师+日期", "must"),
        SlideTemplate("toc", "课程大纲", ["学习目标", "基础知识", "核心内容",
                                            "案例分析", "实践练习", "总结"], "", "must"),
        SlideTemplate("content", "学习目标",
                      ["知识目标", "能力目标", "学完能做什么", "考核方式"],
                      "让学员知道学完能收获什么", "must"),
        SlideTemplate("section", "基础知识", [], "", "must"),
        SlideTemplate("content", "概念介绍",
                      ["核心定义", "关键术语", "基本原理", "与已有知识联系"],
                      "从已知到未知", "must"),
        SlideTemplate("content", "发展历程/背景",
                      ["发展阶段", "重要里程碑", "当前现状"],
                      "时间线或故事引入", "normal"),
        SlideTemplate("section", "核心内容", [], "", "must"),
        SlideTemplate("content", "核心理论/方法",
                      ["核心要点1", "核心要点2", "核心要点3", "核心要点4"],
                      "每页一个核心概念", "must"),
        SlideTemplate("content_list", "操作步骤/流程",
                      ["步骤一", "步骤二", "步骤三", "步骤四", "步骤五"],
                      "步骤清晰，编号有序", "must"),
        SlideTemplate("two_column", "要点对比/辨析",
                      ["概念A特点", "概念B特点"],
                      "易混淆概念对比", "normal"),
        SlideTemplate("section", "案例与实践", [], "", "must"),
        SlideTemplate("content_image", "案例分析",
                      ["案例背景", "问题分析", "解决方案", "经验总结"],
                      "真实案例，图文并茂", "must"),
        SlideTemplate("content", "常见问题/注意事项",
                      ["常见错误", "注意事项", "最佳实践", "避坑指南"],
                      "从学员角度思考", "normal"),
        SlideTemplate("content", "总结回顾",
                      ["核心要点", "知识框架", "延伸阅读", "课后作业"],
                      "帮助学员构建知识体系", "must"),
        SlideTemplate("summary", "Q&A", ["感谢聆听", "提问环节", "联系方式"], "", "must"),
    ],
)

# 产品发布
PRODUCT_LAUNCH = SceneTemplate(
    scene_id="product_launch",
    name="产品发布",
    description="新产品发布/功能上线，注重亮点、体验、用户价值",
    default_slides=10,
    style_hint="creative",
    slides=[
        SlideTemplate("cover", "{topic}", [], "产品名+Slogan+日期", "must"),
        SlideTemplate("quote", "一句话介绍",
                      [], "One more thing... 风格的金句开场", "normal"),
        SlideTemplate("section", "为什么做", [], "", "must"),
        SlideTemplate("content", "市场洞察",
                      ["用户痛点", "市场趋势", "现有方案不足", "我们的思考"],
                      "讲故事，引发共鸣", "must"),
        SlideTemplate("section", "是什么", [], "", "must"),
        SlideTemplate("content_image", "产品概述",
                      ["产品定位", "核心价值", "目标用户", "Slogan"],
                      "大图+简洁文字", "must"),
        SlideTemplate("content", "核心功能",
                      ["亮点功能一", "亮点功能二", "亮点功能三"],
                      "每个功能一页或集中展示", "must"),
        SlideTemplate("content_image", "功能演示",
                      ["功能描述", "使用场景", "用户收益"],
                      "截图/演示图为主", "normal"),
        SlideTemplate("section", "为什么好", [], "", "must"),
        SlideTemplate("data_cards", "核心数据/优势",
                      ["性能提升", "效率提升", "成本降低", "用户好评"],
                      "用数字说话", "must"),
        SlideTemplate("two_column", "对比优势",
                      ["我们的优势", "传统方案不足"],
                      "直观对比", "normal"),
        SlideTemplate("content", "应用场景",
                      ["场景一", "场景二", "场景三"],
                      "场景化描述，让用户代入", "must"),
        SlideTemplate("section", "怎么获得", [], "", "must"),
        SlideTemplate("content", "价格/上线计划",
                      ["版本与价格", "上线时间", "购买方式", "优惠活动"],
                      "清晰的行动号召", "must"),
        SlideTemplate("timeline", "发展路线",
                      ["当前版本", "下阶段", "未来规划"],
                      "展示愿景", "normal"),
        SlideTemplate("summary", "感谢", ["立即体验", "官网/下载", "Q&A"], "", "must"),
    ],
)

# 通用
GENERAL = SceneTemplate(
    scene_id="general",
    name="通用",
    description="通用演示结构，适合各种场合",
    default_slides=8,
    style_hint="professional",
    slides=[
        SlideTemplate("cover", "{topic}", [], "", "must"),
        SlideTemplate("toc", "目录", ["背景介绍", "核心内容", "详细说明", "总结"], "", "must"),
        SlideTemplate("content", "背景介绍",
                      ["背景概述", "现状分析", "核心问题"], "", "must"),
        SlideTemplate("content", "核心内容",
                      ["要点一", "要点二", "要点三"], "", "must"),
        SlideTemplate("content", "详细说明",
                      ["具体内容", "关键细节", "实施路径"], "", "normal"),
        SlideTemplate("data_cards", "数据展示",
                      ["指标一", "指标二", "指标三", "指标四"], "", "normal"),
        SlideTemplate("content", "案例/实践",
                      ["案例介绍", "经验总结"], "", "optional"),
        SlideTemplate("content", "总结与展望",
                      ["主要结论", "后续计划", "行动建议"], "", "must"),
        SlideTemplate("summary", "感谢聆听", [], "", "must"),
    ],
)


SCENE_TEMPLATES = {
    "thesis_defense": THESIS_DEFENSE,
    "business_report": BUSINESS_REPORT,
    "project_intro": PROJECT_INTRO,
    "teaching_training": TEACHING_TRAINING,
    "product_launch": PRODUCT_LAUNCH,
    "general": GENERAL,
}

# 场景别名映射
SCENE_ALIASES = {
    "论文答辩": "thesis_defense", "答辩": "thesis_defense", "学术": "thesis_defense",
    "商业汇报": "business_report", "融资": "business_report", "商业计划": "business_report",
    "项目介绍": "project_intro", "项目汇报": "project_intro", "项目总结": "project_intro",
    "教学培训": "teaching_training", "培训": "teaching_training", "教学": "teaching_training", "课程": "teaching_training",
    "产品发布": "product_launch", "发布会": "product_launch", "新品": "product_launch",
    "通用": "general", "default": "general",
}

# 目标人群配置
AUDIENCE_CONFIG = {
    "expert": {
        "name": "专业人士",
        "depth": "deep",        # 内容深度
        "use_terminology": True,
        "focus_data": True,
        "bullet_style": "technical",
        "adjustments": {
            "add_pages": ["技术细节", "实验数据", "参考文献"],
            "remove_pages": ["基础概念解释"],
            "detail_level": "high",
        },
    },
    "general": {
        "name": "普通听众",
        "depth": "medium",
        "use_terminology": False,
        "focus_data": False,
        "bullet_style": "accessible",
        "adjustments": {
            "add_pages": ["生活案例", "通俗解释"],
            "remove_pages": ["技术细节"],
            "detail_level": "medium",
        },
    },
    "executive": {
        "name": "管理层",
        "depth": "summary",
        "use_terminology": False,
        "focus_data": True,
        "bullet_style": "concise",
        "adjustments": {
            "add_pages": ["执行摘要", "ROI分析", "决策建议"],
            "remove_pages": ["技术细节", "实施步骤"],
            "detail_level": "low",
            "conclusion_first": True,
        },
    },
    "student": {
        "name": "学生",
        "depth": "educational",
        "use_terminology": True,
        "focus_data": False,
        "bullet_style": "educational",
        "adjustments": {
            "add_pages": ["基础知识", "示例", "练习题"],
            "remove_pages": [],
            "detail_level": "high",
            "step_by_step": True,
        },
    },
    "customer": {
        "name": "客户",
        "depth": "value",
        "use_terminology": False,
        "focus_data": True,
        "bullet_style": "benefit",
        "adjustments": {
            "add_pages": ["客户案例", "价值收益", "服务支持"],
            "remove_pages": ["技术细节", "内部流程"],
            "detail_level": "medium",
            "benefit_first": True,
        },
    },
}

AUDIENCE_ALIASES = {
    "专业": "expert", "专家": "expert", "技术": "expert", "工程师": "expert",
    "普通": "general", "大众": "general", "公众": "general",
    "管理层": "executive", "领导": "executive", "老板": "executive", "高管": "executive", "投资人": "executive",
    "学生": "student", "学员": "student", "初学者": "student",
    "客户": "customer", "用户": "customer", "顾客": "customer",
}


# ==========================================
# SlidePlan 数据结构
# ==========================================

@dataclass
class SlidePlanItem:
    """单页计划"""
    index: int
    layout: str
    title: str
    suggested_bullets: List[str] = field(default_factory=list)
    notes: str = ""
    importance: str = "normal"
    content_source: str = ""      # 从文档哪部分提取
    estimated_time: str = ""      # 建议演讲时长

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SlidePlan:
    """PPT 结构计划"""
    topic: str
    scene: str
    scene_name: str
    audience: str
    audience_name: str
    target_slides: int
    actual_slides: int
    style: str
    slides: List[SlidePlanItem] = field(default_factory=list)
    document_summary: str = ""
    key_points: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "meta": {
                "topic": self.topic,
                "scene": self.scene,
                "scene_name": self.scene_name,
                "audience": self.audience,
                "audience_name": self.audience_name,
                "target_slides": self.target_slides,
                "actual_slides": self.actual_slides,
                "style": self.style,
                "document_summary": self.document_summary,
                "key_points": self.key_points,
            },
            "slides": [s.to_dict() for s in self.slides],
        }

    def to_json(self, indent=2, ensure_ascii=False) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=ensure_ascii)

    def to_outline_data(self) -> list:
        """转换为 PPTOrchestrator 可用的 slides_data 格式"""
        result = []
        for s in self.slides:
            item = {"layout": s.layout, "title": s.title}
            if s.suggested_bullets:
                if s.layout == "data_cards":
                    item["data"] = [(b, "—", "") for b in s.suggested_bullets[:4]]
                elif s.layout == "timeline":
                    item["timeline_items"] = [(f"阶段{i+1}", b, "") for i, b in enumerate(s.suggested_bullets[:4])]
                elif s.layout == "two_column":
                    mid = len(s.suggested_bullets) // 2
                    item["left_content"] = s.suggested_bullets[:mid]
                    item["right_content"] = s.suggested_bullets[mid:]
                elif s.layout == "quote":
                    item["quote_text"] = s.suggested_bullets[0] if s.suggested_bullets else s.title
                else:
                    item["bullets"] = s.suggested_bullets
            if s.notes:
                item["notes"] = s.notes
            result.append(item)
        return result


# ==========================================
# SlidePlanner 主类
# ==========================================

class SlidePlanner:
    """
    智能 PPT 结构规划器

    用法:
        planner = SlidePlanner()

        # 仅根据主题
        plan = planner.plan(topic="人工智能发展趋势", scene="project_intro")

        # 指定页数和受众
        plan = planner.plan(
            topic="新产品发布",
            scene="product_launch",
            audience="customer",
            slide_count=12
        )

        # 从文档生成
        plan = planner.plan_from_document(
            topic="论文汇报",
            document_path="paper.docx",
            scene="thesis_defense"
        )

        # 输出 JSON
        print(plan.to_json())

        # 直接生成 PPT
        from office_agent import PPTOrchestrator
        orch = PPTOrchestrator()
        result = orch.generate_from_outline(
            plan.topic, plan.to_outline_data(), style=plan.style
        )
    """

    def __init__(self):
        self.templates = SCENE_TEMPLATES

    def plan(self, topic: str,
             scene: str = "general",
             audience: str = "general",
             slide_count: int = 0,
             style: str = "",
             user_requirements: str = "") -> SlidePlan:
        """
        生成 PPT 结构计划

        Args:
            topic: 主题
            scene: 场景 (thesis_defense/business_report/project_intro/teaching_training/product_launch/general)
                   也支持中文：论文答辩/商业汇报/项目介绍/教学培训/产品发布
            audience: 目标人群 (expert/general/executive/student/customer)
            slide_count: 目标页数（0=使用默认）
            style: 风格 (professional/minimal/creative/tech/academic/nature)
            user_requirements: 用户额外要求（自然语言）
        """
        # 解析场景
        scene_id = self._resolve_scene(scene)
        scene_template = self.templates[scene_id]

        # 解析受众
        audience_id = self._resolve_audience(audience)
        audience_config = AUDIENCE_CONFIG[audience_id]

        # 目标页数
        target = slide_count if slide_count > 0 else scene_template.default_slides

        # 风格
        actual_style = style or scene_template.style_hint

        # 解析用户额外要求
        extra_pages, remove_pages, emphasis = self._parse_requirements(user_requirements)

        # 构建页面列表
        slides = self._build_slides(
            topic=topic,
            scene_template=scene_template,
            audience_config=audience_config,
            target_count=target,
            extra_pages=extra_pages,
            remove_pages=remove_pages,
            emphasis=emphasis,
        )

        plan = SlidePlan(
            topic=topic,
            scene=scene_id,
            scene_name=scene_template.name,
            audience=audience_id,
            audience_name=audience_config["name"],
            target_slides=target,
            actual_slides=len(slides),
            style=actual_style,
            slides=slides,
            key_points=self._extract_key_points(topic, scene_template),
        )

        return plan

    def plan_from_document(self, topic: str,
                           document_path: str,
                           scene: str = "general",
                           audience: str = "general",
                           slide_count: int = 0,
                           style: str = "") -> SlidePlan:
        """
        从文档生成 PPT 结构计划

        支持 .docx 和 .txt 文件
        """
        # 读取文档内容
        content = self._read_document(document_path)

        # 分析文档
        doc_summary = self._summarize_content(content)
        doc_sections = self._extract_sections(content)
        key_points = self._extract_key_points_from_text(content)

        # 生成基础计划
        plan = self.plan(
            topic=topic,
            scene=scene,
            audience=audience,
            slide_count=slide_count,
            style=style,
        )

        # 用文档内容丰富计划
        plan.document_summary = doc_summary
        plan.key_points = key_points or plan.key_points

        # 将文档章节匹配到页面
        self._enrich_with_document(plan, doc_sections, content)

        # 更新页数
        plan.actual_slides = len(plan.slides)

        return plan

    def plan_from_text(self, topic: str, text: str,
                       scene: str = "general",
                       audience: str = "general",
                       slide_count: int = 0,
                       style: str = "") -> SlidePlan:
        """从文本内容生成计划"""
        doc_summary = self._summarize_content(text)
        doc_sections = self._extract_sections(text)
        key_points = self._extract_key_points_from_text(text)

        plan = self.plan(
            topic=topic, scene=scene, audience=audience,
            slide_count=slide_count, style=style,
        )

        plan.document_summary = doc_summary
        plan.key_points = key_points or plan.key_points
        self._enrich_with_document(plan, doc_sections, text)
        plan.actual_slides = len(plan.slides)

        return plan

    # ==========================================
    # 内部方法
    # ==========================================

    def _resolve_scene(self, scene: str) -> str:
        """解析场景标识"""
        if not scene:
            return "general"
        scene_lower = scene.lower().strip()
        if scene_lower in SCENE_TEMPLATES:
            return scene_lower
        if scene in SCENE_ALIASES:
            return SCENE_ALIASES[scene]
        # 模糊匹配
        for alias, sid in SCENE_ALIASES.items():
            if alias in scene:
                return sid
        return "general"

    def _resolve_audience(self, audience: str) -> str:
        """解析受众标识"""
        if not audience:
            return "general"
        audience_lower = audience.lower().strip()
        if audience_lower in AUDIENCE_CONFIG:
            return audience_lower
        if audience in AUDIENCE_ALIASES:
            return AUDIENCE_ALIASES[audience]
        for alias, aid in AUDIENCE_ALIASES.items():
            if alias in audience:
                return aid
        return "general"

    def _parse_requirements(self, requirements: str) -> tuple:
        """解析用户额外要求"""
        if not requirements:
            return [], [], []

        extra = []
        remove = []
        emphasis = []

        # 提取"需要/加/增加XX页"
        for pattern in [
            r"(?:加|增加|添加|需要|要有|补充)([^，。；,;]+?)(?:页|部分|内容|章节)",
            r"(?:加|增加|添加|需要|要有|补充)(?:一个|一页|一张)?([^，。；,;]{2,15})(?:页|，|。|；|;|$)",
        ]:
            matches = re.findall(pattern, requirements)
            for m in matches:
                m = m.strip().lstrip("加")
                if m and len(m) >= 2 and m not in extra:
                    extra.append(m)

        # 提取"不要/去掉XX"
        remove_match = re.findall(r"(?:不要|去掉|删除|不需要|不用)([^，。；,;]+?)(?:页|部分|内容|章节|，|。|；|;|$)", requirements)
        for r in remove_match:
            r = r.strip()
            if r and len(r) >= 2:
                remove.append(r)

        # 提取"重点/强调XX"
        emphasis_match = re.findall(r"(?:重点|强调|突出|注重|侧重)(?:是|在|讲|介绍)?([^，。；,;]+)", requirements)
        for e in emphasis_match:
            e = e.strip()
            if e and len(e) >= 2:
                emphasis.append(e)

        return extra, remove, emphasis

    def _build_slides(self, topic: str,
                      scene_template: SceneTemplate,
                      audience_config: dict,
                      target_count: int,
                      extra_pages: list,
                      remove_pages: list,
                      emphasis: list) -> List[SlidePlanItem]:
        """构建页面列表"""
        slides = []

        # 受众调整
        adjustments = audience_config.get("adjustments", {})

        # 1. 构建模板页面
        for tmpl in scene_template.slides:
            # 检查是否被用户移除
            should_remove = False
            for rp in remove_pages:
                if rp in tmpl.title_key:
                    should_remove = True
                    break
            if should_remove:
                continue

            # 受众调整：移除某些页面
            for remove_hint in adjustments.get("remove_pages", []):
                if remove_hint in tmpl.title_key or remove_hint in (tmpl.notes or ""):
                    should_remove = True
                    break
            if should_remove:
                continue

            title = tmpl.title_key.replace("{topic}", topic)
            bullets = self._adapt_bullets(
                tmpl.bullet_suggestions, audience_config, emphasis
            )
            est_time = self._estimate_time(tmpl.layout, len(bullets))

            slides.append(SlidePlanItem(
                index=0,
                layout=tmpl.layout,
                title=title,
                suggested_bullets=bullets,
                notes=tmpl.notes,
                importance=tmpl.importance,
                estimated_time=est_time,
            ))

        # 2. 受众建议添加的页面
        for add_hint in adjustments.get("add_pages", []):
            if not any(add_hint in s.title for s in slides):
                insert_pos = self._find_insert_position(slides, add_hint)
                slides.insert(insert_pos, SlidePlanItem(
                    index=0,
                    layout="content",
                    title=add_hint,
                    suggested_bullets=self._suggest_bullets_for(add_hint, audience_config),
                    importance="normal",
                    estimated_time="1-2分钟",
                ))

        # 3. 编号
        for i, s in enumerate(slides, 1):
            s.index = i

        # 4. 调整页数到目标（不包含用户额外要求的页）
        slides = self._adjust_to_target(slides, target_count)

        # 5. 添加用户额外要求的页面（最后添加，确保保留）
        seen_titles = set()
        for ep in extra_pages:
            title = re.sub(r"^(加|增加|添加|需要|要有|一个|一页|一张)", "", ep).strip()
            title = re.sub(r"(页|部分|内容|章节)$", "", title).strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)

            summary_idx = next(
                (i for i, s in enumerate(slides) if s.layout == "summary"),
                len(slides)
            )
            slides.insert(summary_idx, SlidePlanItem(
                index=0,
                layout="content",
                title=title,
                suggested_bullets=[f"{title}相关内容"],
                importance="must",
                estimated_time="1-2分钟",
            ))

        # 6. 最终编号
        for i, s in enumerate(slides, 1):
            s.index = i

        return slides

    def _adapt_bullets(self, bullets: list, audience_config: dict, emphasis: list) -> list:
        """根据受众调整要点"""
        if not bullets:
            return []

        style = audience_config.get("bullet_style", "accessible")
        depth = audience_config.get("depth", "medium")

        adapted = []
        for b in bullets:
            if style == "technical":
                # 专业人士：保留技术术语，可更深入
                adapted.append(b)
            elif style == "concise":
                # 管理层：简洁，结论先行
                adapted.append(b if len(b) < 20 else b[:18] + "…")
            elif style == "benefit":
                # 客户：强调收益
                if "优势" in b or "特点" in b or "功能" in b:
                    adapted.append(b +("（带来的价值）" if "价值" not in b else ""))
                else:
                    adapted.append(b)
            elif style == "educational":
                # 学生：更详细，加示例提示
                adapted.append(b)
            else:
                # 普通听众：通俗易懂
                adapted.append(b)

        # 强调的点放前面
        if emphasis:
            for emp in emphasis:
                for i, b in enumerate(adapted):
                    if emp in b and i > 0:
                        adapted.insert(0, adapted.pop(i))
                        break

        return adapted

    def _estimate_time(self, layout: str, bullet_count: int) -> str:
        """估算演讲时长"""
        base_times = {
            "cover": "0.5分钟",
            "toc": "0.5分钟",
            "section": "0.5分钟",
            "content": "2-3分钟",
            "content_image": "2-3分钟",
            "two_column": "2分钟",
            "content_list": "2-3分钟",
            "data_cards": "1-2分钟",
            "timeline": "1-2分钟",
            "quote": "0.5分钟",
            "summary": "1分钟",
        }
        return base_times.get(layout, "1-2分钟")

    def _find_insert_position(self, slides: list, title_hint: str) -> int:
        """找到插入位置"""
        # 在第一个 section 页之后，或在 summary 之前
        for i, s in enumerate(slides):
            if s.layout == "summary":
                return i
        return len(slides)

    def _suggest_bullets_for(self, title: str, audience_config: dict) -> list:
        """为新增页面建议要点"""
        suggestions = {
            "执行摘要": ["核心结论", "关键数据", "决策建议"],
            "ROI分析": ["投入成本", "预期收益", "回报周期"],
            "技术细节": ["实现原理", "关键算法", "性能指标"],
            "实验数据": ["实验设置", "数据结果", "统计分析"],
            "参考文献": ["主要文献1", "主要文献2", "主要文献3"],
            "基础知识": ["基本概念", "术语解释", "前置知识"],
            "示例": ["示例说明", "代码演示", "运行结果"],
            "练习题": ["练习1", "练习2", "练习3"],
            "生活案例": ["日常场景", "实际应用", "效果展示"],
            "通俗解释": ["简单类比", "直观理解", "常见误区"],
            "客户案例": ["客户背景", "使用效果", "客户评价"],
            "价值收益": ["直接收益", "间接收益", "长期价值"],
            "服务支持": ["售后服务", "技术支持", "培训服务"],
        }
        return suggestions.get(title, [f"{title}相关内容"])

    def _adjust_to_target(self, slides: list, target: int) -> list:
        """调整页数到目标数量，保持原始顺序"""
        current = len(slides)

        if current == target:
            return slides

        if current < target:
            # 需要增加页面：在 summary 页前插入
            summary_idx = next(
                (i for i, s in enumerate(slides) if s.layout == "summary"),
                len(slides)
            )
            extra_count = 0
            while len(slides) < target:
                extra_count += 1
                new_slide = SlidePlanItem(
                    index=0,
                    layout="content",
                    title=f"补充内容 {extra_count}",
                    suggested_bullets=["要点一", "要点二", "要点三"],
                    importance="optional",
                    estimated_time="1-2分钟",
                )
                slides.insert(summary_idx, new_slide)
                summary_idx += 1  # 下次还插在 summary 前面
        else:
            # 需要减少页面：保持顺序，优先删除 optional 页
            must_indices = [i for i, s in enumerate(slides) if s.importance == "must"]
            optional_indices = [i for i, s in enumerate(slides) if s.importance != "must"]

            # 需要删除多少 optional 页
            remove_count = current - target

            if remove_count <= len(optional_indices):
                # 从后往前删 optional 页（保留前面的）
                # 但要均匀删除，而不是全删后面的
                if remove_count < len(optional_indices):
                    # 均匀选择要保留的 optional
                    step = len(optional_indices) / (len(optional_indices) - remove_count)
                    keep_optional = set()
                    for j in range(len(optional_indices) - remove_count):
                        keep_optional.add(optional_indices[int(j * step)])
                    # 构建结果：按原始顺序保留
                    result = []
                    for i, s in enumerate(slides):
                        if s.importance == "must" or i in keep_optional:
                            result.append(s)
                    slides = result
                else:
                    # 删除所有 optional
                    slides = [s for s in slides if s.importance == "must"]
            else:
                # must 页也超了，保留前 target 个 must
                slides = [s for s in slides if s.importance == "must"][:target]

        return slides

    # ==========================================
    # 文档处理
    # ==========================================

    def _read_document(self, path: str) -> str:
        """读取文档内容"""
        path = Path(path)
        if not path.exists():
            return ""

        suffix = path.suffix.lower()

        if suffix == ".docx":
            try:
                from docx import Document
                doc = Document(str(path))
                texts = []
                for para in doc.paragraphs:
                    if para.text.strip():
                        texts.append(para.text.strip())
                return "\n".join(texts)
            except Exception:
                return ""
        elif suffix in (".txt", ".md"):
            try:
                for enc in ["utf-8", "gbk", "gb2312", "utf-16"]:
                    try:
                        return path.read_text(encoding=enc)
                    except UnicodeDecodeError:
                        continue
            except Exception:
                return ""
        return ""

    def _summarize_content(self, content: str) -> str:
        """简单内容摘要"""
        if not content:
            return ""
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        if not lines:
            return ""
        # 取前几行作为摘要
        summary_lines = lines[:3]
        return "；".join(summary_lines)[:200]

    def _extract_sections(self, content: str) -> List[Dict[str, Any]]:
        """提取文档章节"""
        sections = []
        current_section = {"title": "前言", "content": [], "level": 0}

        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Markdown 标题
            if line.startswith("# "):
                if current_section["content"]:
                    sections.append(current_section)
                current_section = {"title": line[2:].strip(), "content": [], "level": 1}
            elif line.startswith("## "):
                if current_section["content"]:
                    sections.append(current_section)
                current_section = {"title": line[3:].strip(), "content": [], "level": 2}
            elif line.startswith("### "):
                if current_section["content"]:
                    sections.append(current_section)
                current_section = {"title": line[4:].strip(), "content": [], "level": 3}
            # 中文标题模式
            elif re.match(r"^第[一二三四五六七八九十\d]+[章节部分]", line):
                if current_section["content"]:
                    sections.append(current_section)
                current_section = {"title": line, "content": [], "level": 1}
            elif re.match(r"^[一二三四五六七八九十]+[、.．]", line) and len(line) < 30:
                if current_section["content"]:
                    sections.append(current_section)
                current_section = {"title": line, "content": [], "level": 2}
            elif re.match(r"^\d+[.．、]\s*\S", line) and len(line) < 40:
                # 可能是小节标题
                current_section["content"].append(line)
            else:
                current_section["content"].append(line)

        if current_section["content"] or current_section["title"] != "前言":
            sections.append(current_section)

        return sections

    def _extract_key_points_from_text(self, content: str) -> List[str]:
        """从文本提取关键点"""
        points = []
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            # 列表项
            if re.match(r"^[-*•·]\s+", line):
                point = re.sub(r"^[-*•·]\s+", "", line)
                if len(point) > 5:
                    points.append(point[:60])
            # 数字列表
            elif re.match(r"^\d+[.、)]\s*", line):
                point = re.sub(r"^\d+[.、)]\s*", "", line)
                if len(point) > 5:
                    points.append(point[:60])
            # 包含关键标识词
            elif any(kw in line for kw in ["结论", "发现", "结果", "表明", "证明", "关键", "核心"]):
                points.append(line[:60])

        return points[:10]

    def _extract_key_points(self, topic: str, template: SceneTemplate) -> List[str]:
        """从主题和模板生成关键点"""
        points = []
        for s in template.slides:
            if s.importance == "must" and s.bullet_suggestions:
                points.extend(s.bullet_suggestions[:2])
        return points[:8]

    def _enrich_with_document(self, plan: SlidePlan, sections: list, raw_content: str):
        """用文档内容丰富计划"""
        if not sections:
            return

        # 尝试将文档章节匹配到PPT页面
        section_idx = 0
        for slide in plan.slides:
            if slide.layout in ("content", "content_list", "two_column") and section_idx < len(sections):
                section = sections[section_idx]

                # 如果标题相关，用文档内容替换建议要点
                if (section["title"] != "前言" and
                    (section["title"] in slide.title or slide.title in section["title"])):
                    # 提取要点
                    bullets = []
                    for line in section["content"][:5]:
                        line = line.strip()
                        if line and len(line) > 3:
                            # 去掉列表标记
                            line = re.sub(r"^[-*•·\d.、)）]\s*", "", line)
                            bullets.append(line[:50])
                    if bullets:
                        slide.suggested_bullets = bullets
                        slide.content_source = section["title"]
                    section_idx += 1
                elif section["title"] == "前言" and section_idx == 0:
                    # 前言匹配背景
                    if "背景" in slide.title:
                        bullets = [l.strip()[:50] for l in section["content"][:4] if len(l.strip()) > 5]
                        if bullets:
                            slide.suggested_bullets = bullets
                            slide.content_source = "文档前言"
                    section_idx += 1

    # ==========================================
    # 便捷方法
    # ==========================================

    def list_scenes(self) -> list:
        """列出所有支持的场景"""
        return [
            {"id": sid, "name": t.name, "description": t.description,
             "default_slides": t.default_slides, "style": t.style_hint}
            for sid, t in self.templates.items()
        ]

    def list_audiences(self) -> list:
        """列出所有支持的目标人群"""
        return [
            {"id": aid, "name": cfg["name"], "depth": cfg["depth"]}
            for aid, cfg in AUDIENCE_CONFIG.items()
        ]

    def generate_ppt(self, plan: SlidePlan, output_path: str = ""):
        """直接从计划生成 PPT（便捷方法）"""
        from .ppt_orchestrator import PPTOrchestrator
        orch = PPTOrchestrator()
        return orch.generate_from_outline(
            title=plan.topic,
            slides_data=plan.to_outline_data(),
            style=plan.style,
            output_path=output_path or f"{plan.topic[:20]}.pptx",
        )
