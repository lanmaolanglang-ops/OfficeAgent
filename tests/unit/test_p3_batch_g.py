# -*- coding: utf-8 -*-
"""P3-G 批（杂项/并发/健壮性）回归：108/110/111/112/114/116/117/119/120/
121/124/125/128/129/130/137/143。"""
import logging
import os
import types
from types import MappingProxyType

import pytest


# ---------- P3-112 log level 大小写不敏感 ----------
def test_p3_112_log_level_case_insensitive():
    from office_agent.config_system.schemas import GlobalConfig, LoggingConfig
    cfg = GlobalConfig(logging=LoggingConfig(level="info"))
    assert cfg.logging.level == "INFO"
    cfg2 = GlobalConfig(logging=LoggingConfig(level=" Warning "))
    assert cfg2.logging.level == "WARNING"
    with pytest.raises(Exception):
        GlobalConfig(logging=LoggingConfig(level="VERBOSE"))


# ---------- P3-110/111 prompt 精确优先 + 只取 active ----------
def _cm_with_prompts(prompts, agents=None):
    from office_agent.config_system.config_manager import ConfigManager
    cm = ConfigManager.__new__(ConfigManager)
    cm._prompts = prompts
    cm._agents = agents or {}
    return cm


def test_p3_110_exact_name_preferred():
    cm = _cm_with_prompts({
        "report_excel": [{"content": "substr", "status": "active", "is_default": True}],
        "excel": [{"content": "exact", "status": "active", "is_default": True}],
    })
    assert cm.get_agent_prompt("excel") == "exact"


def test_p3_111_disabled_not_default():
    cm = _cm_with_prompts({"p": [
        {"version": "1", "content": "old", "status": "disabled", "is_default": True},
        {"version": "2", "content": "new", "status": "active", "is_default": False},
    ]})
    got = cm.get_prompt("p")
    assert got["version"] == "2"
    # 显式指定版本仍可取停用版本（管理面需要）
    assert cm.get_prompt("p", "1")["content"] == "old"
    only_disabled = _cm_with_prompts({"q": [
        {"version": "1", "status": "disabled", "is_default": True}]})
    assert only_disabled.get_prompt("q") is None


# ---------- P3-114 上下文入队前不可变快照 ----------
def test_p3_114_context_immutable_snapshot(monkeypatch):
    from office_agent.logging_system import handlers as hmod
    captured = {}

    def fake_submit(func, record):
        captured["ctx"] = record.office_agent_context
        return True

    monkeypatch.setattr(hmod, "submit_background", fake_submit)
    monkeypatch.setattr(
        "office_agent.logging_system.context.get_context_dict",
        lambda: {"request_id": "r1"},
    )
    handler = hmod.DatabaseLogHandler()
    handler.emit(logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", None, None))
    assert isinstance(captured["ctx"], MappingProxyType)
    assert captured["ctx"]["request_id"] == "r1"
    with pytest.raises(TypeError):
        captured["ctx"]["request_id"] = "mutated"


# ---------- P3-116 shutdown 同步兜底排空 ----------
def test_p3_116_shutdown_drains_inline():
    from office_agent.logging_system import background as bg
    done = []
    bg._jobs.put((lambda: done.append(1), (), {}))
    bg.shutdown(timeout=0.1)       # 无 worker → 走 _drain_inline
    assert done == [1]


# ---------- P3-143 队列满时同步落库而非 handleError ----------
def test_p3_143_full_queue_sync_write(monkeypatch):
    from office_agent.logging_system import handlers as hmod
    monkeypatch.setattr(hmod, "submit_background", lambda func, rec: False)
    monkeypatch.setattr(
        "office_agent.logging_system.context.get_context_dict", lambda: {})
    handler = hmod.DatabaseLogHandler()
    calls = []
    handler._write_to_db = lambda rec: calls.append(1)
    handler.handleError = lambda rec: pytest.fail("不应走 handleError 丢日志")
    handler.emit(logging.LogRecord("x", logging.ERROR, __file__, 1, "e", None, None))
    assert calls == [1]


# ---------- P3-117 csv 转换临时名唯一 ----------
def test_p3_117_csv_temp_names_unique(tmp_path, monkeypatch):
    from office_agent.task_queue.tasks import excel_tasks
    csv = tmp_path / "a.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(excel_tasks, "get_output_dir", lambda: tmp_path)
    p1 = excel_tasks._csv_to_xlsx(str(csv))
    p2 = excel_tasks._csv_to_xlsx(str(csv))
    try:
        assert p1 != p2 and os.path.exists(p1) and os.path.exists(p2)
    finally:
        for p in (p1, p2):
            if os.path.exists(p):
                os.remove(p)


# ---------- P3-119 manage.py 用当前解释器跑 alembic ----------
def test_p3_119_manage_uses_sys_executable():
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "manage.py"),
               encoding="utf-8").read()
    assert 'sys.executable, "-m", "alembic"' in src
    assert '["alembic", "-c"' not in src


# ---------- P3-120 win32 接管 SIGBREAK ----------
def test_p3_120_sigbreak_registered():
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "desktop",
                            "app_launcher.py"), encoding="utf-8").read()
    assert "signal.SIGBREAK" in src


# ---------- P3-121 stop_service 校验返回码 ----------
def test_p3_121_stop_service_returncode(monkeypatch):
    from desktop import service_manager
    R = types.SimpleNamespace

    def case(rc, stdout=""):
        monkeypatch.setattr(
            service_manager.subprocess, "run",
            lambda *a, **k: R(returncode=rc, stdout=stdout, stderr=""))
        return service_manager.stop_service()

    assert case(0) is True
    assert case(1062) is True
    assert case(1, "FAILED") is False


# ---------- P3-124/125 空白文本/未 fit 显式报错 ----------
def test_p3_124_125_local_embedders_loud():
    from office_agent.knowledge_base.embeddings import (
        TfidfEmbedder, KeywordEmbedder, InvalidEmbeddingVectorError,
    )
    tf = TfidfEmbedder()
    kw = KeywordEmbedder()
    for emb in (tf, kw):
        with pytest.raises(InvalidEmbeddingVectorError):
            emb.embed_query("   ")
        with pytest.raises(InvalidEmbeddingVectorError):
            emb.embed_query("预算")           # 未 fit
    # fit 后正常
    tf.fit(["预算管理办法", "差旅报销规定"])
    vec = tf.embed_query("预算")
    assert any(v != 0 for v in vec)
    kw.fit(["预算管理办法", "差旅报销规定"])
    assert len(kw.embed_query("预算")) == kw.dimension


# ---------- P3-128 通用“标题不编号”传播到各级 ----------
def test_p3_128_generic_heading_no_numbering():
    from office_agent.parsers.format_parser import FormatRuleParser
    rule = FormatRuleParser().parse("标题不编号")
    for level in range(1, 5):
        assert rule.headings[level]["numbering"] is False


# ---------- P3-129 表格处理描述按实际开关 ----------
def _stub_word_service():
    from office_agent.services.word_service import WordService
    ws = WordService.__new__(WordService)
    ws._table_counter = 0
    ws.changes = []
    ws._apply_three_line_table = lambda t, c: None
    ws._has_existing_caption = lambda t: True   # 已有题注，不再加
    ws._add_table_caption = lambda t, n: None
    return ws


def test_p3_129_table_change_message_respects_flags():
    from office_agent.models.schemas import TableConfig
    doc = types.SimpleNamespace(tables=[object(), object()])

    ws_off = _stub_word_service()
    ws_off._process_tables(doc, TableConfig(three_line=False, auto_number=False))
    assert not any("三线表" in c or "自动编号" in c for c in ws_off.changes)

    ws_on = _stub_word_service()
    ws_on._process_tables(doc, TableConfig(three_line=True, auto_number=True))
    assert any("三线表" in c and "自动编号" in c for c in ws_on.changes)


# ---------- P3-130 默认知识库优先语义 embedder，离线回退 Tfidf ----------
def test_p3_130_default_kb_embedder(tmp_path, monkeypatch):
    from office_agent.knowledge_base import knowledge_base as kbm
    from office_agent.knowledge_base.embeddings import (
        TfidfEmbedder, KeywordEmbedder, EmbeddingBackendUnavailableError,
    )
    import office_agent.knowledge_base.embeddings as emb_mod
    # 配置了语义 embedder（用与 Tfidf 不同的真实 embedder 占位）→ 使用之
    monkeypatch.setattr(emb_mod, "create_semantic_embedder",
                        lambda *a, **k: KeywordEmbedder())
    kb = kbm.create_default_kb(storage_dir=str(tmp_path / "a"))
    assert isinstance(kb.embedder, KeywordEmbedder)

    monkeypatch.setattr(emb_mod, "create_semantic_embedder",
                        lambda *a, **k: (_ for _ in ()).throw(
                            EmbeddingBackendUnavailableError("none")))
    kb2 = kbm.create_default_kb(storage_dir=str(tmp_path / "b"))
    assert isinstance(kb2.embedder, TfidfEmbedder)


# ---------- P3-137 sample_data 内层非标量行规整 ----------
def test_p3_137_sample_data_inner_rows():
    from office_agent.excel_agent.vision_analyzer import ExcelVisionAnalyzer
    an = ExcelVisionAnalyzer.__new__(ExcelVisionAnalyzer)
    table = an._build_table({"sample_data": [["a", "b"], "scalar", {"x": 1, "y": 2}]})
    assert table.sample_data == [["a", "b"], ["scalar"], [1, 2]]
    table2 = an._build_table({"sample_data": "bad"})
    assert table2.sample_data == []


# ---------- P3-108 save_output 常规输出走流式 fileobj ----------
def test_p3_108_save_output_streams(tmp_path):
    from office_agent.storage.storage_service import StorageService
    src = tmp_path / "out.xlsx"
    src.write_bytes(b"hello-binary")
    svc = StorageService.__new__(StorageService)
    calls = {"fileobj": 0, "bytes": 0}

    def fake_fileobj(filename, fileobj, **kw):
        calls["fileobj"] += 1
        assert fileobj.read() == b"hello-binary"
        return "streamed"

    def fake_upload(**kw):
        calls["bytes"] += 1
        return "in-memory"

    svc.upload_fileobj = fake_fileobj
    svc.upload = fake_upload
    assert svc.save_output(str(src)) == "streamed"
    assert calls == {"fileobj": 1, "bytes": 0}
