"""P2-7：document_renderer 临时目录生命周期回归测试。

覆盖重点：renderer 自建临时根目录的释放出口（此前 VisionGateway 长生命
周期持有 renderer 却没有任何 close 出口，每个 gateway 实例在临时目录留下
一个永久残留的 vision_* 根目录）。
"""
import os
import glob
import tempfile
from pathlib import Path

import pytest

from office_agent.vision_gateway.document_renderer import DocumentRenderer
from office_agent.vision_gateway.gateway import VisionGateway


def _make_pdf(path, pages: int = 2):
    """生成一份真实的 N 页 PDF（不 mock 渲染链路）。"""
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"页面内容 {i + 1}")
    doc.save(str(path))
    doc.close()
    return str(path)


def _vision_roots():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "vision_*")))


@pytest.fixture
def pdf_path(tmp_path):
    return _make_pdf(tmp_path / "input.pdf", pages=2)


class TestRendererOwnedRoot:
    def test_constructor_creates_owned_root(self, pdf_path):
        renderer = DocumentRenderer()
        try:
            assert renderer.output_dir
            assert os.path.isdir(renderer.output_dir)
            assert not renderer.closed
        finally:
            renderer.close()

    def test_render_writes_pages_into_root(self, pdf_path):
        renderer = DocumentRenderer(dpi=72, max_pages=5)
        try:
            pages = renderer.render(pdf_path)
            assert len(pages) == 2
            written = list(Path(renderer.output_dir).glob("*.png"))
            assert len(written) == 2
        finally:
            renderer.close()

    def test_close_removes_root(self, pdf_path):
        renderer = DocumentRenderer()
        root = renderer.output_dir
        renderer.render(pdf_path)
        assert os.path.isdir(root)
        renderer.close()
        assert not os.path.exists(root)
        assert renderer.closed

    def test_close_is_idempotent(self, pdf_path):
        renderer = DocumentRenderer()
        renderer.render(pdf_path)
        renderer.close()
        renderer.close()  # 不得抛异常（重复删除防护）
        assert renderer.closed

    def test_context_manager_cleans_up(self, pdf_path):
        with DocumentRenderer() as renderer:
            root = renderer.output_dir
            renderer.render(pdf_path)
            assert os.path.isdir(root)
        assert not os.path.exists(root)


class TestRendererPostCloseSemantics:
    def test_render_after_close_raises(self, pdf_path):
        renderer = DocumentRenderer()
        renderer.close()
        with pytest.raises(RuntimeError, match="已关闭"):
            renderer.render(pdf_path)

    def test_render_bytes_after_close_raises(self):
        renderer = DocumentRenderer()
        renderer.close()
        with pytest.raises(RuntimeError, match="已关闭"):
            renderer.render_images_from_bytes(b"fake", "x.png")

    def test_render_error_does_not_leak_root(self, tmp_path):
        """渲染失败（不支持的格式）后，根目录仍在且仍可被 close 释放。"""
        renderer = DocumentRenderer()
        try:
            bad = tmp_path / "input.rtf"
            bad.write_bytes(b"{\\rtf1}")
            with pytest.raises(ValueError, match="不支持"):
                renderer.render(str(bad))
            assert os.path.isdir(renderer.output_dir)
        finally:
            root = renderer.output_dir
            renderer.close()
            assert not os.path.exists(root)


class TestRendererBorrowedDir:
    def test_borrowed_output_dir_is_not_deleted(self, tmp_path, pdf_path):
        caller_dir = tmp_path / "caller_owned"
        caller_dir.mkdir()
        renderer = DocumentRenderer(output_dir=str(caller_dir))
        assert renderer.render(pdf_path)
        renderer.close()
        # 借用目录归调用方所有，renderer 不得删除它
        assert os.path.isdir(caller_dir)
        assert list(caller_dir.glob("*.png"))

    def test_user_input_file_is_never_removed(self, tmp_path, pdf_path):
        renderer = DocumentRenderer()
        try:
            assert renderer.render(pdf_path)
            assert os.path.exists(pdf_path)
        finally:
            renderer.close()
        assert os.path.exists(pdf_path)


class TestGatewayReleasesRenderer:
    def test_gateway_close_releases_renderer_root(self, pdf_path):
        """核心回归：此前 gateway 无 close，根目录永久残留。"""
        before = _vision_roots()
        gateway = VisionGateway()
        root = gateway.renderer.output_dir
        assert os.path.isdir(root)
        gateway.close()
        assert not os.path.exists(root)
        assert set(_vision_roots()) - before == set()

    def test_gateway_close_is_idempotent(self):
        gateway = VisionGateway()
        gateway.close()
        gateway.close()
        assert gateway.closed

    def test_gateway_context_manager(self, pdf_path):
        before = _vision_roots()
        with VisionGateway() as gateway:
            gateway.renderer.render(pdf_path)
            root = gateway.renderer.output_dir
        assert not os.path.exists(root)
        assert set(_vision_roots()) - before == set()

    def test_many_gateways_leave_no_residue(self, pdf_path):
        """重复创建/关闭 N 个 gateway：临时根目录数量不增长。"""
        before = _vision_roots()
        for _ in range(8):
            gateway = VisionGateway()
            gateway.renderer.render(pdf_path)
            gateway.close()
        assert set(_vision_roots()) - before == set()

    def test_repeated_render_keeps_single_root(self, pdf_path):
        """长生命周期 gateway 重复渲染不得新增根目录。"""
        before = _vision_roots()
        gateway = VisionGateway()
        try:
            root = gateway.renderer.output_dir
            for _ in range(3):
                assert gateway.renderer.render(pdf_path)
            assert gateway.renderer.output_dir == root
            assert set(_vision_roots()) - before == {root}
        finally:
            gateway.close()
        assert set(_vision_roots()) - before == set()

    def test_analyze_document_after_close_is_controlled(self, pdf_path):
        """关闭后继续使用 gateway：受控失败，而不是静默写到错误位置。"""
        gateway = VisionGateway()
        gateway.close()
        result = gateway.analyze_document(pdf_path)
        assert result.error and "已关闭" in result.error
        assert result.pages == []

    def test_gateway_close_disables_renderer_but_keeps_clients(self):
        gateway = VisionGateway()
        gateway.add_openai("key-not-real", name="stub")
        gateway.close()
        assert gateway.clients["stub"] is not None
        assert gateway.renderer.closed
