"""PPT 版式/主题枚举校验专项测试（清单 275）。

根因背景：SlideLayout / PPTTheme 枚举早已定义，但全流程（content_planner /
ppt_orchestrator / slide_designer / slide_planner / quality_checker /
ppt_service）直接使用字符串字面量，拼写错误无任何校验——渲染分发表
renderers.get(layout, _render_content) 会把未知版式静默按 content 渲染。

修复策略（不动流程、不换字面量为枚举成员）：
1. 枚举提供 values()/is_valid()，成为合法字符串的唯一权威来源；
2. SlideContent 构造点严格校验（内部拼写错误快速失败）；
3. LLM JSON 输入接缝显式规范化（warning + content），不静默；
4. 渲染分发与主题选择为防御层（warning + changes 记录 + 回退）；
5. 本文件的 AST 源码守卫钉住：ppt_agent 源码中不得再出现枚举外
   的版式字面量。
"""
import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from office_agent.ppt_agent.models import (
    PPTOutline,
    PPTTheme,
    SlideContent,
    SlideLayout,
)
from office_agent.ppt_agent.ppt_service import PPTService, THEME_COLORS

_PPT_AGENT_DIR = Path(inspect.getfile(PPTService)).parent


class TestEnumAuthority:
    def test_every_enum_value_accepted_by_slide_content(self):
        for value in SlideLayout.values():
            assert SlideContent(layout=value).layout == value

    def test_unknown_layout_raises_at_construction(self):
        with pytest.raises(ValueError, match="未知幻灯片版式"):
            SlideContent(layout="conent")  # 拼写错误必须快速失败

    def test_render_dispatch_covers_exactly_enum_values(self):
        # _render_slide 的分发表必须与 SlideLayout 值一一对应：
        # 新增版式忘注册渲染器（或渲染器键拼错）会立刻暴露
        tree = ast.parse(textwrap.dedent(inspect.getsource(PPTService._render_slide)))
        dispatch_keys = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        dispatch_keys.add(key.value)
        assert dispatch_keys == set(SlideLayout.values())

    def test_theme_colors_cover_all_enum_themes(self):
        assert set(PPTTheme.values()) <= set(THEME_COLORS.keys())


class TestLLMInputSeam:
    def test_llm_unknown_layout_normalized_with_warning(self):
        from office_agent.ppt_agent.content_planner import ContentPlanner

        planner = ContentPlanner.__new__(ContentPlanner)
        data = {
            "slides": [
                {"layout": "title_slide", "title": "封面"},  # LLM 臆造版式
                {"layout": "content", "title": "正文", "bullets": ["a"]},
            ]
        }
        outline = planner._parse_ai_outline(data, "标题", "professional")
        # 未知版式被规范化为 content（显式 warning），不得残留非法值
        layouts = [s.layout for s in outline.slides]
        assert "title_slide" not in layouts
        assert all(SlideLayout.is_valid(layout) for layout in layouts)
        # 标题为“封面”的那一页由 title_slide 规范化而来
        cover_slide = next(s for s in outline.slides if s.title == "封面")
        assert cover_slide.layout == "content"


class TestRenderDefenseLayer:
    def test_unknown_layout_falls_back_with_explicit_record(self, tmp_path):
        service = PPTService()
        slide = SlideContent(layout="content", title="x")
        slide.layout = "weird_layout"  # 构造后赋值绕过校验的场景
        outline = PPTOutline(title="t", slides=[slide])
        result = service.generate(outline, str(tmp_path / "out.pptx"))
        assert Path(result.output_path).exists()
        assert any("未知版式" in c for c in service.changes)

    def test_unknown_theme_falls_back_with_explicit_record(self, tmp_path):
        service = PPTService()
        outline = PPTOutline(title="t", theme="neon_punk",
                             slides=[SlideContent(layout="content", title="x")])
        service.generate(outline, str(tmp_path / "out.pptx"))
        assert any("未知主题" in c for c in service.changes)


class TestSourceGuardNoUnknownLayoutLiterals:
    """AST 源码守卫：ppt_agent 内所有版式字面量必须是 SlideLayout 合法值。

    覆盖：layout="..." 关键字实参、s.layout == "..." 比较、
    layout = "..." 赋值、s.layout in ("...", ...) 成员判断。
    Prompt 示例文本（大字符串常量内部）不在 AST 节点层面出现，不受约束。
    """

    def _layout_literals(self):
        literals = []  # (file, lineno, value)
        for path in sorted(_PPT_AGENT_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "layout":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        literals.append((path.name, node.lineno, node.value.value))
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        name = None
                        if isinstance(target, ast.Name):
                            name = target.id
                        elif isinstance(target, ast.Attribute):
                            name = target.attr
                        if name == "layout" and isinstance(node.value, ast.Constant) \
                                and isinstance(node.value.value, str):
                            literals.append((path.name, node.lineno, node.value.value))
                elif isinstance(node, ast.Compare):
                    left = node.left
                    left_name = None
                    if isinstance(left, ast.Name):
                        left_name = left.id
                    elif isinstance(left, ast.Attribute):
                        left_name = left.attr
                    if left_name != "layout":
                        continue
                    comparators = []
                    for op, comp in zip(node.ops, node.comparators):
                        if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(comp, ast.Constant):
                            comparators.append(comp.value)
                        elif isinstance(op, (ast.In, ast.NotIn)) and isinstance(comp, (ast.Tuple, ast.List)):
                            comparators.extend(
                                e.value for e in comp.elts if isinstance(e, ast.Constant)
                            )
                    for value in comparators:
                        if isinstance(value, str):
                            literals.append((path.name, node.lineno, value))
        return literals

    def test_all_layout_literals_are_valid_enum_values(self):
        valid = set(SlideLayout.values())
        violations = [
            f"{fname}:{lineno} 版式字面量 {value!r} 不在 SlideLayout 中"
            for fname, lineno, value in self._layout_literals()
            if value not in valid
        ]
        assert violations == [], "\n".join(violations)

    def test_guard_actually_collects_literals(self):
        # 守卫自身的有效性：必须真实收集到相当数量的字面量，防 AST 规则失效空转
        assert len(self._layout_literals()) >= 30
