"""ExcelQualityIssue.fixed 字段定义钉住测试（清单 2.5 节低成本核验项）。

清单条目背景：「模块 import 时动态给 dataclass 添加 fixed property；
对 slots 类不安全，且与模型定义耦合」。

源码核验结论：当前实现并不存在该反模式——
``excel_agent/models.py`` 的 ``ExcelQualityIssue`` 是原生 dataclass，
``fixed`` 是声明式字段（models.py:264），无 slots、无 import 时
monkeypatch；``quality_checker.py`` 直接从 models 导入使用。
本文件钉住这一结论，防止未来回退到动态注入。
"""
import dataclasses
import inspect

from office_agent.excel_agent import quality_checker
from office_agent.excel_agent.models import ExcelQualityIssue


def test_fixed_is_declared_dataclass_field_not_property():
    # fixed 是声明式 dataclass 字段，而非事后挂上的 property
    assert not isinstance(getattr(ExcelQualityIssue, "fixed", None), property)
    field_names = {f.name for f in dataclasses.fields(ExcelQualityIssue)}
    assert "fixed" in field_names
    default = ExcelQualityIssue()
    assert default.fixed is False


def test_quality_issue_has_no_slots():
    # 无 __slots__，字段赋值语义与 dataclass 生成器一致
    assert not hasattr(ExcelQualityIssue, "__slots__")
    issue = ExcelQualityIssue(message="x")
    issue.fixed = True
    assert issue.fixed is True
    assert issue.to_dict()["fixed"] is True


def test_no_import_time_property_injection():
    # quality_checker 模块源码不含任何 property 注入 / setattr 补丁
    source = inspect.getsource(quality_checker)
    assert "property(" not in source
    assert "setattr(" not in source
    # ExcelQualityIssue 唯一定义在 models 模块（不存在第二份拷贝）
    assert ExcelQualityIssue.__module__ == "office_agent.excel_agent.models"
    assert quality_checker.ExcelQualityIssue is ExcelQualityIssue
