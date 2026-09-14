"""P1-14 回归测试：fix_outline 结构变更后不得改错页。

历史缺陷：
    ``action["slide"]`` 记录的是**检查时**的 0-based 列表下标。
    ``reduce_bullets`` 会插入续页、``insert_cover`` 会插入封面、
    ``remove_slides`` 会裁剪中间页——这些都会改变列表长度。

    执行完一个插页动作后，后续 action 仍按原始下标取页：
    给原第 5 页的修正会落到原第 4 页上，且被外层"执行成功"掩盖。

修复：
    维护 原始下标 -> 当前下标 的映射，使每个 action 始终作用到它
    检查时看到的那一张页；action 的处理顺序保持不变。

测试全部直接构造 report，不调用真实生成链路。
"""
import copy


from office_agent.ppt_agent.models import PPTOutline, SlideContent
from office_agent.ppt_agent.quality_checker import (
    PPTQualityChecker,
    PPTQualityIssue,
    PPTQualityReport,
)


def _report(*actions, severity="warning"):
    issues = [
        PPTQualityIssue(
            slide_index=-1, issue_type="content", severity=severity,
            message="m", detail="d", fixable=True, fix_action=action,
        )
        for action in actions
    ]
    return PPTQualityReport(issues=issues)


def _outline(n, layout="content", bullets=None):
    """构造 n 页内容页，标题为 S1..Sn，便于断言"到底改了哪一页"。"""
    return PPTOutline(slides=[
        SlideContent(layout=layout, title=f"S{i}",
                     bullets=list(bullets) if bullets is not None else [f"b{j}" for j in range(3)])
        for i in range(1, n + 1)
    ])


def _titles(outline):
    return [s.title for s in outline.slides]


def _notes_of(outline, title):
    return next(s.notes for s in outline.slides if s.title == title)


def _font_of(outline, title):
    return next(s.body_font_size for s in outline.slides if s.title == title)


# ------------------------------------------------- 1. 单次 insert 后后续动作


def test_single_insert_then_later_action_targets_original_slide():
    """原第 2 页插续页后，针对原第 5 页的 reduce_text 仍须落到原第 5 页。"""
    outline = _outline(6)
    report = _report(
        {"type": "reduce_bullets", "slide": 1, "max": 1},
        {"type": "reduce_text", "slide": 4},
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _titles(fixed) == ["S1", "S2", "S2（续 2）", "S3", "S4", "S5", "S6"]
    # 降字号必须落在 S5（原第 5 页），而不是被前移的 S4
    assert _font_of(fixed, "S5") == 16
    assert _font_of(fixed, "S4") is None
    assert _font_of(fixed, "S6") is None


def test_single_insert_later_action_is_unify_font():
    """同一场景换用 unify_font，验证结果一致。"""
    outline = _outline(6)
    report = _report(
        {"type": "reduce_bullets", "slide": 1, "max": 1},
        {"type": "unify_font", "slide": 4},
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert "统一使用大纲字体方案" in (_notes_of(fixed, "S5") or "")
    assert "统一使用大纲字体方案" not in (_notes_of(fixed, "S4") or "")


# --------------------------------------------------------- 2. 两次 insert


def test_two_inserts_then_later_action():
    outline = _outline(7)
    report = _report(
        {"type": "reduce_bullets", "slide": 1, "max": 1},
        {"type": "reduce_bullets", "slide": 3, "max": 1},
        {"type": "reduce_text", "slide": 6},
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _titles(fixed) == [
        "S1", "S2", "S2（续 2）", "S3", "S4", "S4（续 2）", "S5", "S6", "S7",
    ]
    assert _font_of(fixed, "S7") == 16
    assert _font_of(fixed, "S6") is None


# ------------------------------------------- 3. insert 位于后续 target 之前


def test_insert_before_target():
    outline = _outline(5)
    report = _report(
        {"type": "reduce_bullets", "slide": 0, "max": 1},
        {"type": "reduce_font", "slide": 3},
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _font_of(fixed, "S4") == 16
    assert _font_of(fixed, "S3") is None


# ------------------------------------------- 4. insert 位于 target 之后


def test_insert_after_target_does_not_move_target():
    """插页发生在目标之后时，目标位置不变（回归保护）。"""
    outline = _outline(5)
    report = _report(
        {"type": "reduce_font", "slide": 1},
        {"type": "reduce_bullets", "slide": 3, "max": 1},
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _font_of(fixed, "S2") == 16
    assert "S4（续 2）" in _titles(fixed)


# --------------------------------------------------- 5. delete + 后续动作


def test_remove_slides_then_later_action():
    """裁剪中间页后，后续 action 不得落到被删除位置或错位页。"""
    outline = _outline(6)
    report = _report(
        {"type": "remove_slides", "count": 2},   # 裁掉当前位置 1、2 -> S2、S3
        {"type": "reduce_text", "slide": 5},     # 原第 6 页 = S6
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _titles(fixed) == ["S1", "S4", "S5", "S6"]
    assert _font_of(fixed, "S6") == 16


def test_remove_slides_then_action_on_removed_slide_is_skipped():
    """目标页已被裁掉时，动作必须被跳过，而不是落到别的页上。"""
    outline = _outline(6)
    report = _report(
        {"type": "remove_slides", "count": 2},   # 裁掉 S2、S3
        {"type": "reduce_text", "slide": 1},     # 原 S2，已不存在
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _titles(fixed) == ["S1", "S4", "S5", "S6"]
    # S4 现在位于被删掉的位置，但绝不能被当成"原第 2 页"改掉
    assert _font_of(fixed, "S4") is None
    assert _font_of(fixed, "S5") is None


# ------------------------------------------------------ 6. 同一页多个动作


def test_multiple_actions_on_same_slide_after_insert():
    outline = _outline(5)
    report = _report(
        {"type": "reduce_bullets", "slide": 1, "max": 1},
        {"type": "reduce_text", "slide": 4},
        {"type": "unify_font", "slide": 4},
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _font_of(fixed, "S5") == 16
    assert "统一使用大纲字体方案" in (_notes_of(fixed, "S5") or "")


# --------------------------------------------------------- 7. 第一页插入


def test_insert_cover_then_later_action():
    """插入封面后整体 +1，后续 action 必须按原页定位。"""
    outline = _outline(4)
    report = _report(
        {"type": "insert_cover"},
        {"type": "reduce_text", "slide": 2},   # 原 S3
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _titles(fixed)[0] == "演示文稿"
    assert _titles(fixed)[1:] == ["S1", "S2", "S3", "S4"]
    assert _font_of(fixed, "S3") == 16
    assert _font_of(fixed, "S2") is None


def test_insert_cover_after_index_actions_leaves_them_correct():
    """封面插在最后时，先前已应用的修正不会被误改。"""
    outline = _outline(4)
    report = _report(
        {"type": "reduce_text", "slide": 2},
        {"type": "insert_cover"},
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _font_of(fixed, "S3") == 16
    assert _titles(fixed)[0] == "演示文稿"


# ------------------------------------------------------- 8. 最后一页插入


def test_insert_on_last_slide_then_no_later_target():
    outline = _outline(4)
    report = _report({"type": "reduce_bullets", "slide": 3, "max": 1})
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _titles(fixed) == ["S1", "S2", "S3", "S4", "S4（续 2）"]
    assert fixed.slides[-1].bullets == ["b1", "b2"]


def test_append_summary_then_index_action():
    """追加总结页在末尾，不影响既有页下标。"""
    outline = _outline(3)
    report = _report(
        {"type": "append_summary"},
        {"type": "reduce_text", "slide": 1},
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _titles(fixed) == ["S1", "S2", "S3", "感谢聆听"]
    assert _font_of(fixed, "S2") == 16


# --------------------------------------- 9. renumber 不改变真实 action target


def test_renumber_only_rewrites_page_numbers():
    outline = _outline(3)
    PPTQualityChecker._renumber(outline)
    assert [s.page_number for s in outline.slides] == [1, 2, 3]
    assert _titles(outline) == ["S1", "S2", "S3"]


def test_inserted_continuation_is_renumbered():
    outline = _outline(3)
    report = _report({"type": "reduce_bullets", "slide": 0, "max": 1})
    fixed = PPTQualityChecker().fix_outline(outline, report)
    assert [s.page_number for s in fixed.slides] == [1, 2, 3, 4]


# --------------------------------------------- 10. 无结构变化时原行为不变


def test_no_structural_change_keeps_original_behaviour():
    outline = _outline(4)
    report = _report(
        {"type": "reduce_text", "slide": 2},
        {"type": "unify_font_all"},
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _titles(fixed) == ["S1", "S2", "S3", "S4"]
    assert _font_of(fixed, "S3") == 16
    for slide in fixed.slides:
        assert "统一使用大纲字体方案" in (slide.notes or "")


def test_fix_outline_is_copy_on_write():
    """不得就地修改传入的 outline。"""
    outline = _outline(3)
    original = copy.deepcopy(outline)
    report = _report(
        {"type": "reduce_bullets", "slide": 0, "max": 1},
        {"type": "reduce_text", "slide": 2},
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    assert _titles(outline) == _titles(original)
    assert fixed is not outline


def test_add_title_targets_original_slide_after_insert():
    """add_title 同样必须命中原页。"""
    outline = _outline(5)
    outline.slides[3].title = ""
    report = _report(
        {"type": "reduce_bullets", "slide": 1, "max": 1},
        {"type": "add_title", "slide": 3},   # 原 S4（无标题）
    )
    fixed = PPTQualityChecker().fix_outline(outline, report)

    # 插页后 S4 位于下标 4，标题应为"第5页"
    assert _titles(fixed) == ["S1", "S2", "S2（续 2）", "S3", "第5页", "S5"]
