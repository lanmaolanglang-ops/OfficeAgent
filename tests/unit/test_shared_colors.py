"""office_agent.colors 共享颜色解析 + 任务优先级映射的针对性测试。

根因背景：Excel(hex_to_color) 与 PPT(hex_to_rgb) 曾各自实现十六进制
解析，口径漂移（alpha 处理、错误消息、3 位缩写支持）；任务优先级在
chat.py 传字面量 1、task.py 传 "normal"，跨层转换规则散落。
"""
import pytest

from office_agent.colors import parse_hex_argb, parse_hex_color


class TestParseHexColor:
    @pytest.mark.parametrize("value,expected", [
        ("#FF0000", (255, 0, 0, 255)),
        ("ff0000", (255, 0, 0, 255)),
        ("#F00", (255, 0, 0, 255)),          # 3 位缩写
        ("80FF0000", (255, 0, 0, 128)),      # 8 位 AARRGGBB（alpha 在前）
        (" #0a1B2c ", (10, 27, 44, 255)),    # 空白容错 + 大小写混合
    ])
    def test_valid_forms(self, value, expected):
        assert parse_hex_color(value) == expected

    @pytest.mark.parametrize("value", [
        "", "#12", "#12345", "GGGGGG", "#FF00000", 123, None,
    ])
    def test_invalid_raises_with_input_in_message(self, value):
        with pytest.raises(ValueError) as exc_info:
            parse_hex_color(value)
        if isinstance(value, str):
            assert repr(value) in str(exc_info.value)

    def test_six_digit_defaults_to_opaque_alpha(self):
        assert parse_hex_argb("#FF0000") == "FFFF0000"
        assert parse_hex_argb("00ff00") == "FF00FF00"

    def test_argb_uppercase_and_alpha_preserved(self):
        assert parse_hex_argb("80ff0000") == "80FF0000"
        assert parse_hex_argb("#abcdef80") == "ABCDEF80"

    def test_three_digit_argb(self):
        assert parse_hex_argb("#F00") == "FFFF0000"


class TestExcelPptColorParity:
    """两个产品的库专属转换入口必须共享同一解析口径。"""

    def test_excel_hex_to_color_delegates(self):
        from office_agent.excel_agent.excel_service import hex_to_color
        color = hex_to_color("#FF0000")
        assert color.rgb == "FFFF0000"

    def test_ppt_hex_to_rgb_delegates(self):
        from office_agent.ppt_agent.ppt_service import hex_to_rgb
        rgb = hex_to_rgb("#FF0000")
        assert (rgb[0], rgb[1], rgb[2]) == (255, 0, 0)

    def test_same_input_same_channels(self):
        from office_agent.excel_agent.excel_service import hex_to_color
        from office_agent.ppt_agent.ppt_service import hex_to_rgb
        argb = hex_to_color("#0a1B2c").rgb          # FF0A1B2C
        rgb = hex_to_rgb("#0a1B2c")
        assert (int(argb[2:4], 16), int(argb[4:6], 16), int(argb[6:8], 16)) == tuple(rgb)

    @pytest.mark.parametrize("bad", ["#12", "GGGGGG", 42])
    def test_both_reject_identically(self, bad):
        from office_agent.excel_agent.excel_service import hex_to_color
        from office_agent.ppt_agent.ppt_service import hex_to_rgb
        with pytest.raises(ValueError):
            hex_to_color(bad)
        with pytest.raises(ValueError):
            hex_to_rgb(bad)


class TestPriorityMapping:
    def test_single_mapping_source(self):
        from office_agent.task_queue import (
            DEFAULT_PRIORITY, PRIORITY_TO_INT, VALID_PRIORITIES,
        )
        assert DEFAULT_PRIORITY == "normal"
        assert set(VALID_PRIORITIES) == {"high", "normal", "low"}
        assert PRIORITY_TO_INT == {"high": 2, "normal": 1, "low": 0}
        # 顺序单调：优先级越高整数越大
        assert PRIORITY_TO_INT["high"] > PRIORITY_TO_INT["normal"] > PRIORITY_TO_INT["low"]

    def test_routers_no_longer_carry_literal_maps(self):
        """路由层不得再散落优先级字面量映射表。"""
        import inspect
        from office_agent.api.router import chat, task
        chat_src = inspect.getsource(chat)
        task_src = inspect.getsource(task)
        for forbidden in ('{"high": 2', "'high': 2", "priority=1,"):
            assert forbidden not in chat_src
            assert forbidden not in task_src
