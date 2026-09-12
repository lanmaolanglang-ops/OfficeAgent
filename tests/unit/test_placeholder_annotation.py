"""P5-12：占位产物标注回归。

P5-12 的三个历史子项中，``image_generation/config.py`` 的非原子
``json.dump`` 已随 P3-13 改为 ``atomic_write_json``（不复改）；
``knowledge_base.py`` 全部持久化走 ``_atomic_json_write``，无直写。

本轮剩余子项：PPT 渲染失败的页面会生成"文本大纲占位图"（python-pptx
无法栅格化，所有 PPT 页都走此路径），但占位图与 ``text_hint`` 均未
标注"这是合成图"——视觉模型会把占位图当真实幻灯片，对版式/图表/
配色凭空幻觉。现图片右下角直接绘制占位标注，且 ``text_hint`` 前缀
标注随提示词进入视觉请求。
"""
import pytest
from docx import Document as DocxDocument  # noqa: F401
from pptx import Presentation
from pptx.util import Inches

from office_agent.vision_gateway.document_renderer import DocumentRenderer


@pytest.fixture
def renderer(tmp_path, monkeypatch):
    renderer = DocumentRenderer(output_dir=str(tmp_path / "render"))
    # 本机若装有 LibreOffice，pptx 会优先走真实 PDF 渲染；
    # 占位渲染图只存在于"无 LibreOffice"降级路径——正是本条要标注的场景。
    monkeypatch.setattr(renderer, "_convert_pptx_to_pdf",
                        lambda file_path: None)
    return renderer


def _make_pptx(path, *, with_picture=False):
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "季度业务汇报"
    body = slide.placeholders[1]
    body.text = "第一要点"
    para = body.text_frame.add_paragraph()
    para.text = "第二要点"
    if with_picture:
        from pptx.util import Emu
        # 形状类型 13（Picture）的文本大纲占位标记
        slide.shapes.add_textbox(Emu(0), Emu(0), Emu(100), Emu(100)).text_frame.text = "[图片]"
    prs.save(str(path))


class TestPlaceholderAnnotation:
    def test_placeholder_image_carries_annotation(self, renderer, tmp_path):
        """占位图必须自带"占位渲染图"角标（画在图上）。"""
        pptx = tmp_path / "deck.pptx"
        _make_pptx(pptx)
        pages = renderer.render(str(pptx))
        assert pages, "应产出占位页"
        for page in pages:
            from PIL import Image
            img = Image.open(page.image.path)
            # 角标以灰色文字绘制在右下角：提取像素区域验证非纯白
            width, height = img.size
            corner = img.crop((width - 460, height - 40, width, height))
            colors = corner.getcolors(maxcolors=100000)
            # 纯白角落只有 1 种颜色；有标注则至少出现灰色系像素
            non_white = [c for c in colors if c[1][:3] < (250, 250, 250)]
            assert non_white, "占位图右下角必须绘制占位标注文字"

    def test_text_hint_leads_with_placeholder_notice(self, renderer, tmp_path):
        """text_hint 必须以占位说明开头，随提示词告知视觉模型。"""
        pptx = tmp_path / "deck.pptx"
        _make_pptx(pptx)
        pages = renderer.render(str(pptx))
        assert pages[0].text_hint.startswith("（占位渲染图：仅含文本大纲")
        assert "季度业务汇报" in pages[0].text_hint, "原始大纲文本保留"

    def test_text_outline_content_preserved(self, renderer, tmp_path):
        pptx = tmp_path / "deck.pptx"
        _make_pptx(pptx)
        pages = renderer.render(str(pptx))
        hint = pages[0].text_hint
        assert "第一要点" in hint and "第二要点" in hint

    def test_multiple_pages_all_annotated(self, renderer, tmp_path):
        pptx = tmp_path / "deck.pptx"
        prs = Presentation()
        for _ in range(4):
            prs.slides.add_slide(prs.slide_layouts[1])
        prs.save(str(pptx))
        pages = renderer.render(str(pptx))
        assert len(pages) == 4
        assert all(p.text_hint.startswith("（占位渲染图") for p in pages)
