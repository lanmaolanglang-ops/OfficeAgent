"""
Content Planner - PPT 内容规划器

负责：
1. 从主题生成 PPT 大纲（封面、目录、章节、内容、总结）
2. 从文本/Word 文档提取内容并分配到各页
3. 可选 LLM 生成更丰富的内容
4. 每页结构：标题、要点、备注
"""
import re
import json
import logging
from pathlib import Path
from typing import Optional, List

from .models import PPTOutline, SlideContent, SlideLayout

logger = logging.getLogger("office_agent.ppt.content_planner")


# ==========================================
# 主题 → 大纲模板
# ==========================================

THEME_TEMPLATES = {
    "项目汇报": [
        ("cover", "项目汇报", "{subtitle}"),
        ("toc", "目录", ""),
        ("section", "项目背景", ""),
        ("content", "背景与目标", ["项目立项背景", "核心目标与KPI", "预期成果"]),
        ("content", "现状分析", ["当前进展", "存在问题", "面临挑战"]),
        ("section", "实施方案", ""),
        ("content", "技术方案", ["技术架构", "核心模块", "关键技术选型"]),
        ("content", "实施计划", ["里程碑节点", "资源分配", "时间安排"]),
        ("data_cards", "关键数据", []),
        ("section", "成果与展望", ""),
        ("content", "阶段成果", ["已完成工作", "核心产出", "价值体现"]),
        ("summary", "总结与展望", ["感谢支持", "Q&A"]),
    ],
    "产品介绍": [
        ("cover", "产品介绍", "{subtitle}"),
        ("toc", "目录", ""),
        ("content", "产品概述", ["产品定位", "目标用户", "核心价值"]),
        ("content", "市场背景", ["市场规模", "用户痛点", "竞品分析"]),
        ("content_image", "核心功能", ["功能亮点一", "功能亮点二", "功能亮点三"]),
        ("two_column", "产品优势", []),
        ("data_cards", "核心数据", []),
        ("content", "应用场景", ["场景一", "场景二", "场景三"]),
        ("timeline", "发展规划", []),
        ("summary", "感谢聆听", []),
    ],
    "工作总结": [
        ("cover", "工作总结", "{subtitle}"),
        ("toc", "目录", ""),
        ("content", "工作概述", ["工作背景", "职责范围", "总体评价"]),
        ("content", "重点工作", ["重点任务一", "重点任务二", "重点任务三"]),
        ("data_cards", "工作成果", []),
        ("content", "经验总结", ["成功经验", "不足之处", "改进方向"]),
        ("content", "下一步计划", ["工作计划", "个人成长", "资源需求"]),
        ("summary", "感谢", []),
    ],
    "培训课件": [
        ("cover", "培训课件", "{subtitle}"),
        ("toc", "课程大纲", ""),
        ("section", "基础知识", ""),
        ("content", "概念介绍", ["核心定义", "基本原理", "关键术语"]),
        ("content", "理论框架", ["理论体系", "核心模型", "发展历程"]),
        ("section", "实践应用", ""),
        ("content", "方法步骤", ["操作流程", "注意事项", "常见问题"]),
        ("content_list", "案例分析", []),
        ("two_column", "要点对比", []),
        ("content", "总结回顾", ["核心要点", "学习建议", "延伸阅读"]),
        ("summary", "Q&A", []),
    ],
    "通用": [
        ("cover", "{title}", "{subtitle}"),
        ("toc", "目录", ""),
        ("content", "背景介绍", ["背景概述", "现状分析", "核心问题"]),
        ("content", "核心内容", ["要点一", "要点二", "要点三"]),
        ("content", "详细说明", ["具体内容", "关键细节", "实施路径"]),
        ("data_cards", "数据展示", []),
        ("content", "总结", ["主要结论", "后续计划", "行动建议"]),
        ("summary", "感谢聆听", []),
    ],
}

# 数据卡片默认数据
DEFAULT_DATA = [
    ("增长率", "126", "%"),
    ("用户数", "50", "万"),
    ("满意度", "98", "%"),
    ("完成度", "100", "%"),
]

# 时间线默认数据
DEFAULT_TIMELINE = [
    ("Q1", "启动", "项目立项"),
    ("Q2", "开发", "核心功能"),
    ("Q3", "测试", "质量保障"),
    ("Q4", "上线", "正式发布"),
]


class ContentPlanner:
    """
    PPT 内容规划器

    用法:
        planner = ContentPlanner()
        outline = planner.plan_from_theme("AI项目汇报", slide_count=10)
        outline = planner.plan_from_text(text_content)
        outline = planner.plan_from_word("document.docx")
    """

    def __init__(self, model_gateway=None):
        self.model_gateway = model_gateway

    def plan_from_theme(self, theme: str,
                        slide_count: int = 10,
                        style: str = "professional",
                        subtitle: str = "",
                        author: str = "") -> PPTOutline:
        """
        从主题生成 PPT 大纲

        优先使用 LLM 生成真实内容；LLM 不可用时回退到模板。
        """
        # 1. 清理标题：从用户指令中提取真正的主题
        clean_title = self._extract_title(theme)

        outline = PPTOutline(
            title=clean_title,
            subtitle=subtitle,
            author=author,
            theme=style,
        )

        # 2. 尝试用 LLM 生成完整大纲
        ai_outline = None
        if self.model_gateway:
            ai_outline = self._generate_with_ai(clean_title, slide_count, style)

        if ai_outline and ai_outline.slides:
            return ai_outline

        # A configured LLM failure must be visible.  Returning a generic
        # template makes the user believe the requested content was created
        # by the model when it was not.
        if self.model_gateway:
            raise RuntimeError("PPT内容模型调用失败，已阻止使用通用模板冒充AI结果")

        # 3. 仅在明确没有配置模型时使用模板
        logger.warning(f"未配置LLM，使用模板生成: {clean_title}")
        return self._generate_from_template(clean_title, slide_count, style, subtitle, author)

    def plan_from_text(self, text: str,
                       style: str = "professional",
                       title: str = "") -> PPTOutline:
        """
        从文本内容生成 PPT 大纲

        支持格式：
        - # 标题 → 封面
        - ## 章节名 → 章节页
        - ### 页标题 → 内容页
        - 普通段落 → 要点
        - - 或 • → 列表项
        """
        outline = PPTOutline(
            title=title or "演示文稿",
            theme=style,
        )

        lines = text.strip().split("\n")
        current_slide = None
        current_bullets = []

        for line in lines:
            line = line.rstrip()
            if not line.strip():
                continue

            # Markdown 标题
            if line.startswith("# ") and not line.startswith("## "):
                # 封面
                if current_slide:
                    current_slide.bullets = current_bullets
                    outline.add_slide(current_slide)
                cover = SlideContent(
                    layout="cover",
                    title=line[2:].strip(),
                    subtitle=title,
                )
                outline.add_slide(cover)
                current_slide = None
                current_bullets = []
                continue

            if line.startswith("## "):
                # 章节页
                if current_slide:
                    current_slide.bullets = current_bullets
                    outline.add_slide(current_slide)
                section = SlideContent(
                    layout="section",
                    title=line[3:].strip(),
                )
                outline.add_slide(section)
                current_slide = None
                current_bullets = []
                continue

            if line.startswith("### "):
                # 内容页
                if current_slide:
                    current_slide.bullets = current_bullets
                    outline.add_slide(current_slide)
                current_slide = SlideContent(
                    layout="content",
                    title=line[4:].strip(),
                )
                current_bullets = []
                continue

            # 列表项
            if re.match(r"^[-*•·]\s+", line):
                bullet = re.sub(r"^[-*•·]\s+", "", line).strip()
                current_bullets.append(bullet)
                continue

            # 数字列表
            if re.match(r"^\d+[.、)]\s*", line):
                bullet = re.sub(r"^\d+[.、)]\s*", "", line).strip()
                current_bullets.append(bullet)
                continue

            # 普通文本 → 如果有当前页，作为要点；否则新建内容页
            if current_slide:
                if len(line) > 10:
                    current_bullets.append(line.strip())
            else:
                current_slide = SlideContent(
                    layout="content",
                    title=line.strip()[:30],
                )

        # 最后一页
        if current_slide:
            current_slide.bullets = current_bullets
            outline.add_slide(current_slide)

        # 确保有封面
        if not outline.slides or outline.slides[0].layout != "cover":
            cover = SlideContent(layout="cover", title=outline.title)
            outline.slides.insert(0, cover)

        # 添加总结页
        has_summary = any(s.layout == "summary" for s in outline.slides)
        if not has_summary:
            outline.add_slide(SlideContent(layout="summary", title="感谢聆听"))

        # 重新编号
        for i, s in enumerate(outline.slides):
            s.page_number = i + 1

        return outline

    def plan_from_word(self, docx_path: str,
                       style: str = "professional") -> PPTOutline:
        """
        从 Word 文档生成 PPT 大纲

        利用已有的 DocumentStructureAnalyzer 分析文档结构，
        将章节标题转为 PPT 页面。
        """
        from docx import Document
        from ..parsers.document_structure import DocumentStructureAnalyzer

        doc = Document(docx_path)
        analyzer = DocumentStructureAnalyzer()
        tree = analyzer.analyze(doc)

        outline = PPTOutline(
            title=tree.title or Path(docx_path).stem,
            theme=style,
        )

        # 封面
        outline.add_slide(SlideContent(
            layout="cover",
            title=tree.title or Path(docx_path).stem,
        ))

        # 目录
        chapters = tree.get_chapters()
        if chapters:
            toc_items = [c.text for c in chapters]
            outline.add_slide(SlideContent(
                layout="toc",
                title="目录",
                bullets=toc_items,
            ))

        # 按章节组织内容
        for chapter in chapters:
            # 章节过渡页
            outline.add_slide(SlideContent(
                layout="section",
                title=chapter.text,
            ))

            # 收集章节下的内容
            sections = chapter.find_children("section")
            if sections:
                for section in sections:
                    bullets = []
                    # 收集子节点文本
                    for child in section.children:
                        if child.type == "paragraph" and child.text:
                            text = child.text.strip()
                            if len(text) > 5:
                                bullets.append(text[:80])
                        elif child.type == "list_item" and child.text:
                            bullets.append(child.text.strip())

                    if not bullets:
                        # 从原文找
                        bullets = [f"{section.text}相关内容"]

                    outline.add_slide(SlideContent(
                        layout="content",
                        title=section.text,
                        bullets=bullets[:5],  # 每页最多5个要点
                    ))
            else:
                # 没有子节，直接用章节内容
                paragraphs = chapter.find_children("paragraph")
                bullets = [p.text.strip()[:80] for p in paragraphs
                           if p.text and len(p.text.strip()) > 10][:5]
                if not bullets:
                    bullets = [f"{chapter.text}相关内容"]
                outline.add_slide(SlideContent(
                    layout="content",
                    title=chapter.text,
                    bullets=bullets,
                ))

        # 总结
        outline.add_slide(SlideContent(layout="summary", title="感谢聆听"))

        return outline

    def plan_from_outline_data(self, title: str, slides_data: list,
                               style: str = "professional") -> PPTOutline:
        """
        从结构化数据生成大纲

        slides_data: [{"layout": "content", "title": "...", "bullets": [...]}]
        """
        outline = PPTOutline(title=title, theme=style)

        for data in slides_data:
            slide = SlideContent(
                layout=data.get("layout", "content"),
                title=data.get("title", ""),
                subtitle=data.get("subtitle", ""),
                bullets=data.get("bullets", []),
                body_text=data.get("body_text", ""),
                left_content=data.get("left_content", []),
                right_content=data.get("right_content", []),
                image_path=data.get("image_path", ""),
                data=data.get("data", []),
                quote_text=data.get("quote_text", ""),
                quote_source=data.get("quote_source", ""),
                timeline_items=data.get("timeline_items", []),
                notes=data.get("notes", ""),
            )
            outline.add_slide(slide)

        return outline

    # ==========================================
    # 辅助方法
    # ==========================================

    def _match_template(self, theme: str) -> list:
        """匹配最接近的模板"""
        theme_lower = theme.lower()

        # 关键词匹配
        keywords = {
            "项目汇报": ["项目", "汇报", "进展", "里程碑"],
            "产品介绍": ["产品", "介绍", "发布", "功能"],
            "工作总结": ["总结", "工作", "年度", "季度", "复盘"],
            "培训课件": ["培训", "课件", "教程", "课程", "学习"],
        }

        for template_name, kws in keywords.items():
            for kw in kws:
                if kw in theme_lower:
                    return THEME_TEMPLATES[template_name]

        return THEME_TEMPLATES["通用"]

    def _adjust_slide_count(self, outline: PPTOutline, target: int) -> PPTOutline:
        """调整页数到目标数量"""
        current = len(outline.slides)

        if current == target:
            return outline

        if current < target:
            # 需要增加内容页
            content_layouts = ["content", "content_list", "two_column"]
            idx = 0
            insert_pos = len(outline.slides) - 1  # 在总结页前插入

            while len(outline.slides) < target:
                layout = content_layouts[idx % len(content_layouts)]
                slide = SlideContent(
                    layout=layout,
                    title=f"补充内容 {idx + 1}",
                    bullets=["要点一", "要点二", "要点三"],
                )
                if layout == "data_cards":
                    slide.data = DEFAULT_DATA
                outline.slides.insert(insert_pos, slide)
                insert_pos += 1
                idx += 1
        else:
            # 需要减少页数，保留封面、目录、章节页、总结页
            essential = []
            removable = []
            for s in outline.slides:
                if s.layout in ("cover", "toc", "section", "summary"):
                    essential.append(s)
                else:
                    removable.append(s)

            # 保留必要页 + 部分内容页
            keep_count = target - len(essential)
            if keep_count > 0:
                # 均匀选取内容页
                step = max(1, len(removable) // keep_count)
                kept = removable[::step][:keep_count]
                result = essential[:2]  # 封面+目录
                result.extend(kept)
                result.extend(essential[2:])  # 章节+总结
                outline.slides = result[:target]
            else:
                outline.slides = essential[:target]

        # 重新编号
        for i, s in enumerate(outline.slides):
            s.page_number = i + 1

        return outline

    def _enrich_with_ai(self, outline: PPTOutline, theme: str) -> PPTOutline:
        """使用 AI 丰富内容（可选）"""
        if not self.model_gateway:
            return outline

        try:
            # 为内容页生成更具体的要点
            for slide in outline.slides:
                if slide.layout in ("content", "content_list") and slide.title:
                    if not slide.bullets or slide.bullets == ["要点一", "要点二", "要点三"]:
                        prompt = (
                            f"为PPT主题'{theme}'的页面'{slide.title}'"
                            f"生成3-5个要点，每行一个，不要编号。"
                        )
                        result = self.model_gateway.chat(
                            user_message=prompt,
                            task_type_str="ppt_content",
                            temperature=0.7,
                        )
                        if result and result.success:
                            bullets = [
                                line.strip().lstrip("-•·* ")
                                for line in result.content.split("\n")
                                if line.strip() and len(line.strip()) > 3
                            ]
                            if bullets:
                                slide.bullets = bullets[:5]
        except Exception:
            pass

        return outline

    # ==========================================
    # 新的智能内容生成方法
    # ==========================================

    def _extract_title(self, raw: str) -> str:
        """从用户指令中提取干净的PPT标题"""
        if not raw:
            return "演示文稿"
        title = raw.strip()
        # 去掉常见前缀
        for prefix in ["生成一份", "生成一个", "帮我生成", "做一个", "做一份", "创建一个",
                        "帮我做", "请生成", "请帮我", "生成", "做", "创建", "写一份", "写一个"]:
            if title.startswith(prefix):
                title = title[len(prefix):].strip()
                break
        # 去掉常见后缀
        for suffix in ["的PPT", "的ppt", "PPT", "ppt", "演示文稿", "幻灯片",
                        "相关内容联网搜索", "联网搜索", "相关内容"]:
            if title.endswith(suffix):
                title = title[:-len(suffix)].strip()
        # 去掉"关于"前缀
        if title.startswith("关于"):
            title = title[2:].strip()
        return title if title else raw.strip()

    def _generate_with_ai(self, title: str, slide_count: int, style: str) -> Optional[PPTOutline]:
        """使用LLM生成完整PPT大纲"""
        try:
            prompt = f"""请为主题"{title}"生成一份{slide_count}页的PPT大纲，风格为{style}。

要求：
1. 每页包含真实、具体、有信息量的内容，不要使用占位符
2. 内容要紧扣主题，有实际价值
3. 每页的要点要具体、详实，不要写"要点一"这种空话

请严格按以下JSON格式返回，不要返回其他内容：
{{
  "subtitle": "副标题（一句话概括）",
  "slides": [
    {{
      "layout": "cover",
      "title": "{title}",
      "subtitle": "副标题"
    }},
    {{
      "layout": "toc",
      "title": "目录",
      "bullets": ["目录项1", "目录项2", "目录项3", "目录项4", "目录项5"]
    }},
    {{
      "layout": "content",
      "title": "具体的页面标题",
      "bullets": ["具体要点1（有实际内容）", "具体要点2（有实际内容）", "具体要点3（有实际内容）"]
    }},
    {{
      "layout": "data_cards",
      "title": "关键数据",
      "data": [["指标名称1", "数值", "单位"], ["指标名称2", "数值", "单位"], ["指标名称3", "数值", "单位"], ["指标名称4", "数值", "单位"]]
    }},
    {{
      "layout": "summary",
      "title": "总结",
      "bullets": ["总结要点1", "总结要点2", "展望"]
    }}
  ]
}}

可用layout类型: cover(封面), toc(目录), section(章节页), content(内容页), two_column(两栏), data_cards(数据卡片), timeline(时间线), summary(总结)
请确保生成{slide_count}页左右，内容充实专业。"""

            result = self.model_gateway.chat(
                user_message=prompt,
                task_type_str="ppt_content",
                temperature=0.7,
                max_tokens=4000,
            )

            if not result or not result.success or not result.content:
                err = (result.error if result and getattr(result, "error", None) else "") or "unknown"
                logger.warning("LLM返回为空: %s", err)
                return None

            # 解析JSON
            content = result.content.strip()
            # 去掉可能的markdown代码块标记
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            data = json.loads(content)
            return self._parse_ai_outline(data, title, style)

        except json.JSONDecodeError as e:
            logger.warning(f"AI返回的JSON解析失败: {e}")
            return None
        except Exception as e:
            logger.warning(f"AI生成大纲失败: {e}")
            return None

    def _parse_ai_outline(self, data: dict, title: str, style: str) -> PPTOutline:
        """解析AI返回的JSON为PPTOutline"""
        outline = PPTOutline(
            title=title,
            subtitle=data.get("subtitle", ""),
            theme=style,
        )

        for slide_data in data.get("slides", []):
            layout = slide_data.get("layout", "content")
            slide = SlideContent(
                layout=layout,
                title=slide_data.get("title", ""),
                subtitle=slide_data.get("subtitle", ""),
                bullets=slide_data.get("bullets", []) or [],
            )

            # 数据卡片
            if layout == "data_cards" and slide_data.get("data"):
                slide.data = [tuple(d) for d in slide_data["data"] if len(d) >= 3]
            elif layout == "data_cards":
                slide.data = DEFAULT_DATA

            # 时间线
            if layout == "timeline" and slide_data.get("timeline"):
                slide.timeline_items = [tuple(t) for t in slide_data["timeline"]]
            elif layout == "timeline":
                slide.timeline_items = DEFAULT_TIMELINE

            # 两栏内容
            if slide_data.get("left_content"):
                slide.left_content = slide_data["left_content"]
            if slide_data.get("right_content"):
                slide.right_content = slide_data["right_content"]

            outline.add_slide(slide)

        # 确保至少有封面和总结
        if not outline.slides:
            return self._generate_from_template(title, 10, style, "", "")

        if outline.slides[0].layout != "cover":
            cover = SlideContent(layout="cover", title=title, subtitle=data.get("subtitle", ""))
            outline.slides.insert(0, cover)

        has_summary = any(s.layout == "summary" for s in outline.slides)
        if not has_summary:
            outline.add_slide(SlideContent(layout="summary", title="感谢聆听",
                                           bullets=["谢谢观看", "Q&A"]))

        # 重新编号
        for i, s in enumerate(outline.slides):
            s.page_number = i + 1

        return outline

    def _generate_from_template(self, theme: str, slide_count: int,
                                 style: str, subtitle: str, author: str) -> PPTOutline:
        """模板回退方案（原逻辑）"""
        template = self._match_template(theme)

        outline = PPTOutline(
            title=theme,
            subtitle=subtitle,
            author=author,
            theme=style,
        )

        for layout, title, bullets in template:
            actual_title = title.replace("{title}", theme).replace("{subtitle}", subtitle or theme)
            actual_bullets = []

            if isinstance(bullets, list):
                actual_bullets = bullets
            elif isinstance(bullets, str) and bullets:
                actual_bullets = [bullets]

            slide = SlideContent(
                layout=layout,
                title=actual_title,
                bullets=actual_bullets,
            )

            if layout == "data_cards" and not actual_bullets:
                slide.data = DEFAULT_DATA
            elif layout == "timeline" and not actual_bullets:
                slide.timeline_items = DEFAULT_TIMELINE
            elif layout == "cover":
                slide.subtitle = subtitle or ""
                slide.notes = author or ""

            outline.add_slide(slide)

        if slide_count and len(outline.slides) != slide_count:
            outline = self._adjust_slide_count(outline, slide_count)

        return outline
