"""P1-13 回归测试：视觉分析的数值与 JSON 解析不得因脏数据整体失败。

D1. priority 裸 ``int(...)``
    同文件已有宽容解析 ``_to_int``，但 ``_build_analyses`` 里仍写
    ``priority=int(a_data.get("priority", 3))``。模型返回
    ``"high"`` / ``None`` / ``""`` / ``"3.0"`` 时会抛 ValueError 或
    TypeError，让整段视觉分析一起失败。

D2. ``_extract_json`` 不感知 JSON 字符串
    朴素括号计数会把字符串内的 ``{`` / ``}`` 当成结构括号，在字符串
    内部的 ``}`` 处提前收尾，截出无法解析的碎片；``\\"`` 与 ``\\\\``
    同理会让引号判定错位。

全部为纯解析测试：不调用任何视觉模型 API。
"""
import json

import pytest

from office_agent.excel_agent.vision_analyzer import ExcelVisionAnalyzer


# ============================================================ D1. priority


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1, 1),
        (5, 5),
        ("1", 1),
        (" 2 ", 2),
        (3.0, 3),
        ("3.0", 3),  # float-like string：不得抛异常
    ],
    ids=["int-1", "int-5", "str-1", "str-padded-2", "float-3", "float-like-str"],
)
def test_to_int_accepts_numeric_shapes(raw, expected):
    assert ExcelVisionAnalyzer._to_int({"priority": raw}, "priority", 3) == expected


@pytest.mark.parametrize(
    "raw", [None, "", "high", "N/A", "未知", [], {}],
    ids=["none", "empty", "word", "na", "cjk", "list", "dict"],
)
def test_to_int_falls_back_to_default_for_junk(raw):
    """非法文本一律回退默认值，而不是抛异常。"""
    assert ExcelVisionAnalyzer._to_int({"priority": raw}, "priority", 3) == 3


def test_to_int_treats_bool_as_junk():
    """bool 是 int 子类，但不是合法优先级，必须回退默认值。"""
    assert ExcelVisionAnalyzer._to_int({"priority": True}, "priority", 3) == 3
    assert ExcelVisionAnalyzer._to_int({"priority": False}, "priority", 3) == 3


def test_to_int_missing_key_uses_default():
    assert ExcelVisionAnalyzer._to_int({}, "priority", 3) == 3


@pytest.mark.parametrize(
    "raw", [None, "", "high", "3.0", " 2 ", "N/A"],
    ids=["none", "empty", "word", "float-like", "padded", "na"],
)
def test_dirty_priority_does_not_break_analysis_building(raw):
    """一个脏 priority 不得让整段 analyses 构建失败。"""
    analyzer = ExcelVisionAnalyzer()
    analyses = analyzer._build_analyses({
        "suggested_analyses": [
            {"analysis_type": "summary", "title": "a", "priority": raw},
            {"analysis_type": "trend", "title": "b", "priority": 1},
        ]
    })
    assert len(analyses) == 2
    assert {a.title for a in analyses} == {"a", "b"}


def test_dirty_priority_falls_back_to_default_three():
    analyzer = ExcelVisionAnalyzer()
    analyses = analyzer._build_analyses({
        "suggested_analyses": [
            {"analysis_type": "summary", "title": "a", "priority": "high"},
        ]
    })
    assert analyses[0].priority == 3


def test_valid_priorities_still_sort_ascending():
    """1 最高：排序语义不得回归。"""
    analyzer = ExcelVisionAnalyzer()
    analyses = analyzer._build_analyses({
        "suggested_analyses": [
            {"title": "low", "priority": 5},
            {"title": "high", "priority": 1},
            {"title": "mid", "priority": 3},
        ]
    })
    assert [a.title for a in analyses] == ["high", "mid", "low"]


# ============================================================ D2. _extract_json


def test_plain_json_object():
    text = '{"a": 1, "b": "x"}'
    assert ExcelVisionAnalyzer._extract_json(text) == text


def test_fenced_json_block():
    text = '```json\n{"a": 1}\n```'
    assert json.loads(ExcelVisionAnalyzer._extract_json(text)) == {"a": 1}


def test_prose_before_json():
    text = '这是分析结果：\n{"a": 1}'
    assert json.loads(ExcelVisionAnalyzer._extract_json(text)) == {"a": 1}


def test_brace_inside_string_value():
    """字符串内的 { 不得被当成结构括号。"""
    data = {"text": "这里有 {变量} 需要替换"}
    text = json.dumps(data, ensure_ascii=False)
    assert json.loads(ExcelVisionAnalyzer._extract_json(text)) == data


def test_closing_brace_inside_string_value():
    """字符串内的 } 不得提前收尾。"""
    data = {"text": "先 } 再结束"}
    text = json.dumps(data, ensure_ascii=False)
    assert json.loads(ExcelVisionAnalyzer._extract_json(text)) == data


def test_escaped_quotes_and_braces_inside_string():
    """\\" 与 {} 组合：转义状态必须被正确跟踪。"""
    raw = '{"text": "他说 \\"{x}\\" 然后结束", "n": 2}'
    result = ExcelVisionAnalyzer._extract_json(raw)
    assert json.loads(result) == {"text": '他说 "{x}" 然后结束', "n": 2}


def test_escaped_backslash_before_quote():
    """\\\\" 是转义的反斜杠 + 字符串结束，不能被当成转义引号。"""
    raw = '{"path": "C:\\\\", "ok": true}'
    result = ExcelVisionAnalyzer._extract_json(raw)
    assert json.loads(result) == {"path": "C:\\", "ok": True}


def test_nested_object():
    data = {"a": {"b": {"c": 1}}, "d": 2}
    text = json.dumps(data)
    assert json.loads(ExcelVisionAnalyzer._extract_json(text)) == data


def test_nested_array_of_objects():
    """数组内的对象同样含花括号，不得被误判。"""
    data = {"rows": [{"id": 1}, {"id": 2}], "n": 2}
    text = json.dumps(data, ensure_ascii=False)
    assert json.loads(ExcelVisionAnalyzer._extract_json(text)) == data


def test_braces_in_prose_then_valid_json():
    """说明文字里含花括号时，应继续往后找到真正的 JSON。"""
    text = '变量 {x} 已处理，结果：{"a": 1}'
    assert json.loads(ExcelVisionAnalyzer._extract_json(text)) == {"a": 1}


@pytest.mark.parametrize(
    "text",
    [
        "",
        "没有 JSON",
        '{"a": 1',           # 未闭合
        '{"a": "未闭合}',    # 字符串未闭合
        "{{{{",
        "}}}}",
    ],
    ids=["empty", "no-json", "unclosed-object", "unclosed-string",
         "only-open", "only-close"],
)
def test_malformed_input_fails_controllably(text):
    """畸形输入必须返回空串（受控失败），而不是截出随机片段。"""
    assert ExcelVisionAnalyzer._extract_json(text) == ""


def test_parse_response_returns_none_for_malformed():
    """_parse_response 对畸形输入返回 None，不得抛异常。"""
    analyzer = ExcelVisionAnalyzer()
    assert analyzer._parse_response('{"a": 1') is None
    assert analyzer._parse_response('前言 {"a": "未闭合}') is None


def test_parse_response_handles_braces_in_strings():
    """端到端：字符串含花括号时仍能解析出完整对象。"""
    analyzer = ExcelVisionAnalyzer()
    data = {"table_type": "report", "notes": "含 {占位} 与 } 符号",
            "columns": [{"name": "金额"}]}
    parsed = analyzer._parse_response(json.dumps(data, ensure_ascii=False))
    assert parsed == data
