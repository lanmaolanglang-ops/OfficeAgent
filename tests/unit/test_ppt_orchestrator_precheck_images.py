"""PPT 预检覆盖与配图候选身份回归测试（P1-16）。

16A：生成前预检此前只有 generate_from_theme 一条路径会跑。
16B：配图候选用 `slide not in candidates` 做值相等判重，
     内容相同的两页会被误判成同一页，后一页永远拿不到配图。
"""
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from office_agent.ppt_agent.models import (
    PPTGenerationResult, PPTOutline, SlideContent,
)
from office_agent.ppt_agent.ppt_orchestrator import PPTOrchestrator


# ------------------------------------------------------------------
# 16A 预检覆盖
# ------------------------------------------------------------------

def _spy(orch, monkeypatch):
    """包住 _pre_check_and_fix 统计调用次数，行为仍走真实实现。"""
    calls = []
    original = orch._pre_check_and_fix

    def spy(outline, expected_slides=None):
        calls.append(expected_slides)
        return original(outline, expected_slides)

    monkeypatch.setattr(orch, "_pre_check_and_fix", spy)
    return calls


def _stub_common(orch, monkeypatch, outline):
    monkeypatch.setattr(orch.service, "generate",
                        lambda o, p: PPTGenerationResult(
                            success=True, output_path=p, slide_count=len(o.slides)))
    monkeypatch.setattr(orch.quality_checker, "check",
                        lambda output_path, expected_slides=None: SimpleNamespace(
                            score=90.0, issues=[], to_dict=lambda: {}))
    return outline


@pytest.fixture
def orch(monkeypatch):
    o = PPTOrchestrator()
    return o


def test_theme_path_runs_precheck(orch, monkeypatch, tmp_path):
    calls = _spy(orch, monkeypatch)

    outline = PPTOutline(title="主题")
    outline.add_slide(SlideContent(layout="cover", title="主题"))
    outline.add_slide(SlideContent(layout="content", title="内容"))
    monkeypatch.setattr(orch.planner, "plan_from_theme",
                        lambda *a, **k: outline)
    _stub_common(orch, monkeypatch, outline)

    res = orch.generate_from_theme(theme="主题", output_path=str(tmp_path / "a.pptx"))
    assert res.success
    assert len(calls) == 1, "theme 路径应恰好预检一次"


def test_text_path_runs_precheck(orch, monkeypatch, tmp_path):
    calls = _spy(orch, monkeypatch)

    outline = PPTOutline(title="文本")
    outline.add_slide(SlideContent(layout="cover", title="文本"))
    outline.add_slide(SlideContent(layout="content", title="内容"))
    monkeypatch.setattr(orch.planner, "plan_from_text", lambda *a, **k: outline)
    _stub_common(orch, monkeypatch, outline)

    res = orch.generate_from_text(text="# 标题", output_path=str(tmp_path / "b.pptx"))
    assert res.success
    assert len(calls) == 1, "text 路径此前完全没有预检"


def test_word_path_runs_precheck(orch, monkeypatch, tmp_path):
    calls = _spy(orch, monkeypatch)

    outline = PPTOutline(title="Word")
    outline.add_slide(SlideContent(layout="cover", title="Word"))
    outline.add_slide(SlideContent(layout="content", title="内容"))
    monkeypatch.setattr(orch.planner, "plan_from_word", lambda *a, **k: outline)
    _stub_common(orch, monkeypatch, outline)

    src = tmp_path / "x.docx"
    src.write_bytes(b"PK\x03\x04")

    res = orch.generate_from_word(docx_path=str(src),
                                  output_path=str(tmp_path / "c.pptx"))
    assert res.success
    assert len(calls) == 1


def test_outline_path_runs_precheck(orch, monkeypatch, tmp_path):
    calls = _spy(orch, monkeypatch)

    outline = PPTOutline(title="大纲")
    outline.add_slide(SlideContent(layout="cover", title="大纲"))
    outline.add_slide(SlideContent(layout="content", title="内容"))
    monkeypatch.setattr(orch.planner, "plan_from_outline_data",
                        lambda *a, **k: outline)
    _stub_common(orch, monkeypatch, outline)

    res = orch.generate_from_outline(
        title="大纲",
        slides_data=[{"layout": "content", "title": "内容"}],
        output_path=str(tmp_path / "d.pptx"),
    )
    assert res.success
    assert len(calls) == 1


def test_template_path_runs_precheck(orch, monkeypatch, tmp_path):
    calls = _spy(orch, monkeypatch)

    outline = PPTOutline(title="模板")
    outline.add_slide(SlideContent(layout="cover", title="模板"))
    outline.add_slide(SlideContent(layout="content", title="内容"))
    monkeypatch.setattr(orch.planner, "plan_from_theme", lambda *a, **k: outline)
    monkeypatch.setattr(orch.template_analyzer, "analyze", lambda path: None)
    monkeypatch.setattr(orch.template_analyzer, "apply_to_outline",
                        lambda path, o: o)
    _stub_common(orch, monkeypatch, outline)
    monkeypatch.setattr(Path, "exists", lambda self: True)

    res = orch.generate_with_template(
        template_path=str(tmp_path / "t.pptx"), theme="模板",
        output_path=str(tmp_path / "e.pptx"),
    )
    assert res.success
    assert len(calls) == 1


def test_precheck_is_not_executed_twice(orch, monkeypatch, tmp_path):
    """每条入口只走一次预检，不能因为新增调用点而重复修正。"""
    calls = _spy(orch, monkeypatch)

    outline = PPTOutline(title="主题")
    outline.add_slide(SlideContent(layout="cover", title="主题"))
    for i in range(3):
        outline.add_slide(SlideContent(layout="content", title=f"页{i}"))
    monkeypatch.setattr(orch.planner, "plan_from_theme", lambda *a, **k: outline)
    _stub_common(orch, monkeypatch, outline)

    orch.generate_from_theme(theme="主题", output_path=str(tmp_path / "f.pptx"))
    assert calls == [10], f"预检调用次数异常: {calls}"


def test_precheck_failure_is_controlled(orch, monkeypatch, tmp_path):
    """预检本身抛错时降级为"不修正"，记录原因，不炸掉整次生成。"""
    import office_agent.ppt_agent.ppt_orchestrator as mod

    outline = PPTOutline(title="主题")
    outline.add_slide(SlideContent(layout="cover", title="主题"))
    outline.add_slide(SlideContent(layout="content", title="内容"))
    monkeypatch.setattr(orch.planner, "plan_from_theme", lambda *a, **k: outline)
    _stub_common(orch, monkeypatch, outline)
    monkeypatch.setattr(mod, "check_and_fix_outline",
                        lambda o, expected: (_ for _ in ()).throw(RuntimeError("预检炸了")))

    res = orch.generate_from_theme(theme="主题", output_path=str(tmp_path / "g.pptx"))
    assert res.success is True, f"预检失败不应让整次生成失败: {res.message}"
    assert any("跳过" in c for c in outline.changes)


def test_precheck_exception_inside_checker_is_recorded(orch, monkeypatch, tmp_path):
    """_pre_check_and_fix 内部 check_and_fix_outline 抛错时，
    返回原大纲并把跳过原因写进 changes，而不是假装修正成功。"""
    import office_agent.ppt_agent.ppt_orchestrator as mod

    outline = PPTOutline(title="主题")
    outline.add_slide(SlideContent(layout="cover", title="主题"))
    monkeypatch.setattr(mod, "check_and_fix_outline",
                        lambda o, expected: (_ for _ in ()).throw(RuntimeError("坏")))

    fixed = orch._pre_check_and_fix(outline)
    assert fixed is outline
    assert any("质量预检" in c and "跳过" in c for c in outline.changes)


# ------------------------------------------------------------------
# 16B 配图候选身份
# ------------------------------------------------------------------

class _FakeGateway:
    """默认返回真实临时文件（成功路径）；fail=True 时模拟生图失败。"""

    def __init__(self, fail=False):
        self.prompts = []
        self.fail = fail

    def available(self):
        return True

    def generate(self, prompt, output_dir=None):
        self.prompts.append(prompt)
        if self.fail:
            return None
        fd, path = tempfile.mkstemp(suffix=".png", dir=output_dir)
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        return path


def _orch_with_gateway(gw, max_images=5):
    o = PPTOrchestrator(image_gateway=gw, max_generated_images=max_images)
    return o


def test_identical_slides_are_both_candidates():
    """内容完全相同的两页是两页，不能因为 dataclass 值相等被合并。"""
    a = SlideContent(layout="content", title="同一页", bullets=["要点"])
    b = SlideContent(layout="content", title="同一页", bullets=["要点"])
    assert a == b, "前提：dataclass 值相等"

    gw = _FakeGateway()
    orch = _orch_with_gateway(gw)
    outline = PPTOutline(title="T", slides=[a, b])

    orch._generate_marked_images(outline)

    assert len(gw.prompts) == 2, "两页都应尝试配图，不能只配一页"


def test_three_slides_two_identical():
    gw = _FakeGateway()
    orch = _orch_with_gateway(gw)
    s1 = SlideContent(layout="content", title="A", bullets=["1"])
    s2 = SlideContent(layout="content", title="B", bullets=["2"])
    s3 = SlideContent(layout="content", title="A", bullets=["1"])  # 与 s1 值相等
    outline = PPTOutline(title="T", slides=[s1, s2, s3])

    orch._generate_marked_images(outline)
    assert len(gw.prompts) == 3


def test_same_slide_object_not_duplicated():
    """同一页重复出现在列表里时（同一对象）也只应尝试一次。"""
    gw = _FakeGateway()
    orch = _orch_with_gateway(gw)
    s = SlideContent(layout="content_image", title="图文页", bullets=["x"])
    outline = PPTOutline(title="T", slides=[s])

    orch._generate_marked_images(outline)
    assert len(gw.prompts) == 1


def test_content_image_pages_take_priority():
    gw = _FakeGateway()
    orch = _orch_with_gateway(gw)
    plain = SlideContent(layout="content", title="普通", bullets=["x"])
    marked = SlideContent(layout="content_image", title="图文", bullets=["y"])
    outline = PPTOutline(title="T", slides=[plain, marked])

    orch._generate_marked_images(outline)
    assert len(gw.prompts) == 2
    assert "图文" in gw.prompts[0], "LLM 标记的图文页应排在前面"


def test_max_generated_images_still_respected():
    gw = _FakeGateway()
    orch = _orch_with_gateway(gw, max_images=2)
    slides = [SlideContent(layout="content", title=f"页{i}", bullets=["x"])
              for i in range(5)]
    outline = PPTOutline(title="T", slides=slides)

    orch._generate_marked_images(outline)
    assert len(gw.prompts) == 2, "上限仍然生效"


def test_slides_with_existing_image_are_skipped():
    gw = _FakeGateway()
    orch = _orch_with_gateway(gw)
    has_img = SlideContent(layout="content", title="已有图", bullets=["x"],
                           image_path="/tmp/a.png")
    no_img = SlideContent(layout="content", title="无图", bullets=["y"])
    outline = PPTOutline(title="T", slides=[has_img, no_img])

    orch._generate_marked_images(outline)
    assert len(gw.prompts) == 1
    assert "无图" in gw.prompts[0]
