"""P3-11：PPT 页数解析、convert_format 死代码清理回归。

修复前：
- ``_parse_slide_count`` 用 ``(\\d{1,3})\\s*页`` 裸匹配——"修改第 3 页"
  这类对既有页的引用被误判成"只要 3 页"，英文 "N slides" 完全不识别；
- ``convert_format`` 在无条件 ``raise`` 之后还留着不可达的
  progress.update(100, "转换完成")（死代码）。
"""
import pytest

from office_agent.task_queue.tasks.file_tasks import convert_format
from office_agent.task_queue.tasks.ppt_tasks import (
    DEFAULT_SLIDES,
    MAX_SLIDES,
    _parse_slide_count,
)


class TestSlideCountParsing:
    @pytest.mark.parametrize("text,expected", [
        ("帮我做一个10页的PPT", 10),
        ("做 10 页 演示", 10),
        ("共10页的汇报", 10),
        ("做一份 15 slides 的 deck", 15),
        ("1 slide 即可", 1),
        ("10-12页", 12),
        ("做3页的简介", 3),
    ])
    def test_parses_expected_counts(self, text, expected):
        assert _parse_slide_count(text, "") == expected

    def test_page_reference_is_not_total_count(self):
        """回归核心："修改第 3 页" 是页引用，不是只要 3 页。"""
        assert _parse_slide_count("帮我修改第 3 页的标题", "") == DEFAULT_SLIDES
        assert _parse_slide_count("把第10页删掉", "") == DEFAULT_SLIDES

    def test_defaults_and_bounds(self):
        assert _parse_slide_count("", "") == DEFAULT_SLIDES == 10
        assert _parse_slide_count("做0页", "") == 1, "下限钳制为 1"
        assert _parse_slide_count("做999页", "") == MAX_SLIDES == 50, \
            "上限钳制为产品 MAX_SLIDES"


class TestConvertFormatDeadCode:
    def test_honest_failure_and_no_phantom_progress(self, tmp_path):
        """死代码已删：不可达的 progress(100) 不再存在。"""
        source = tmp_path / "whatever.docx"
        source.write_bytes(b"doc")
        updates = []

        class _Progress:
            def update(self, value, message=""):
                updates.append(value)

        with pytest.raises(RuntimeError, match="暂未实现"):
            convert_format(str(source), str(tmp_path / "out.pdf"), "pdf",
                           progress=_Progress())
        assert updates == [10, 30], "转换从未完成，不得上报 100"
