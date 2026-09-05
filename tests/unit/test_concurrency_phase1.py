"""Concurrency Phase 1 专项测试（确定性：Event / 显式状态转移，无 timing 依赖）。

覆盖本批三个根修复：
1. chat 路由准备阶段（会话恢复全表扫描 + 文件 stat + 建库记录）卸载到
   工作线程，不阻塞事件循环；Session 不跨线程复用。
2. 日志后台写线程：单个写任务失败不得杀死唯一写线程，且异常必须留痕。
3. LibreOffice docx→pdf 转换：独立 profile + 超时整树终止 + reap +
   降级留痕，不产生持有 profile 锁的孤儿进程。
"""
import asyncio
import logging
import subprocess
import threading
from types import SimpleNamespace

import pytest

from office_agent.api.router import chat as chat_module
from office_agent.api.schemas.request import ChatRequest
from office_agent.logging_system import background as bg
from office_agent.quality import visual_checker as vc


# ---------------------------------------------------------------------------
# 1. chat 路由：准备阶段卸载到工作线程
# ---------------------------------------------------------------------------


class _FakeTaskRepository:
    def __init__(self, session):
        self.session = session

    def create_task(self, **kwargs):
        return SimpleNamespace(id="task_conc_phase1")


class _FakeFileRepository:
    def __init__(self, session):
        self.session = session

    def get_by_id(self, file_id):
        return None


def _install_chat_fakes(monkeypatch, entered, release, state):
    """把 chat 准备阶段的所有外部依赖换成确定性 fake。

    fake session_scope 在进入时记录所在线程并阻塞，直到测试显式放行，
    从而可以精确断言「DB 工作期间事件循环仍然响应」。
    """

    class _FakeSession:
        def __enter__(self):
            state["session_thread"] = threading.get_ident()
            entered.set()
            # 兜底超时，防止断言失败时工作线程永久挂起
            release.wait(timeout=10)
            return self

        def __exit__(self, exc_type, exc, tb):
            state["session_closed_thread"] = threading.get_ident()
            return False

    monkeypatch.setattr(
        "office_agent.database.session.session_scope",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "office_agent.database.repository.TaskRepository",
        _FakeTaskRepository,
    )
    monkeypatch.setattr(
        "office_agent.database.repository.FileRepository",
        _FakeFileRepository,
    )
    submitted = {}

    def _fake_submit_task(**kwargs):
        submitted["kwargs"] = kwargs
        return kwargs.get("task_id")

    monkeypatch.setattr(
        "office_agent.task_queue.submit_task",
        _fake_submit_task,
    )
    monkeypatch.setattr("office_agent.task_queue.init_worker", lambda: None)
    monkeypatch.setattr(
        "office_agent.task_queue.queue_name_for_task_type",
        lambda task_type: "word.format",
    )
    return submitted


def test_chat_prepare_offloaded_and_loop_responsive(monkeypatch):
    """DB/文件准备工作在工作线程执行，阻塞期间事件循环仍可调度。"""
    entered = threading.Event()
    release = threading.Event()
    state = {}
    submitted = _install_chat_fakes(monkeypatch, entered, release, state)

    async def scenario():
        loop_thread = threading.get_ident()
        req = ChatRequest(message="把这份 Word 文档排版一下")
        request = SimpleNamespace(state=SimpleNamespace())
        chat_task = asyncio.create_task(chat_module.chat(req, request))

        # 等待准备工作在（另一个）线程进入 fake session
        assert await asyncio.to_thread(entered.wait, 5), "准备工作未进入 session"
        assert not chat_task.done()

        # DB 被阻塞期间事件循环必须仍能调度其他协程
        await asyncio.wait_for(asyncio.sleep(0.01), timeout=2)

        release.set()
        result = await asyncio.wait_for(chat_task, timeout=5)
        return loop_thread, result

    loop_thread, result = asyncio.run(scenario())

    assert state["session_thread"] != loop_thread, "Session 不得留在事件循环线程"
    assert state["session_closed_thread"] == state["session_thread"], (
        "Session 必须在创建它的工作线程内关闭"
    )
    assert result.data.task_id == "task_conc_phase1"
    assert result.data.status == "queued"
    assert submitted["kwargs"]["task_id"] == "task_conc_phase1"


def test_chat_prepare_propagates_ownership_error(monkeypatch):
    """准备工作中的 HTTPException（如越权文件）必须原样传回事件循环。"""
    state = {}

    class _GuardedSession:
        def __enter__(self):
            state["session_thread"] = threading.get_ident()
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "office_agent.database.session.session_scope",
        lambda: _GuardedSession(),
    )
    monkeypatch.setattr(
        "office_agent.database.repository.TaskRepository",
        _FakeTaskRepository,
    )
    monkeypatch.setattr(
        "office_agent.database.repository.FileRepository",
        _FakeFileRepository,
    )
    # 非管理员用户提交不属于自己的文件 → _require_owned_files 抛 403
    monkeypatch.setattr(chat_module.settings, "auth_enabled", True)

    async def scenario():
        req = ChatRequest(message="排版", file_ids=["file_not_mine"])
        request = SimpleNamespace(
            state=SimpleNamespace(user_id="user_1", user_role="user")
        )
        return await chat_module.chat(req, request)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(scenario())
    assert exc_info.value.status_code == 403
    assert state["session_thread"] != threading.get_ident()


# ---------------------------------------------------------------------------
# 2. 日志后台写线程：失败任务不得杀死线程，且异常留痕
# ---------------------------------------------------------------------------


class _ListHandler(logging.Handler):
    def __init__(self, records):
        super().__init__()
        self._records = records

    def emit(self, record):
        self._records.append(record)


def test_background_writer_survives_failing_job():
    records = []
    handler = _ListHandler(records)
    bg._logger.addHandler(handler)
    done = threading.Event()

    def failing_job():
        raise RuntimeError("simulated db write failure")

    def marker_job():
        done.set()

    try:
        assert bg.submit(failing_job)
        assert bg.submit(marker_job)
        # 失败任务之后的任务仍被消费 → 写线程存活
        assert done.wait(timeout=5), "失败任务杀死了后台写线程"
        worker = bg._worker
        assert worker is not None and worker.is_alive()
        assert any(
            "后台日志写任务失败" in record.getMessage() for record in records
        ), "后台写任务异常必须留痕，不得静默吞掉"
    finally:
        bg._logger.removeHandler(handler)


def test_background_writer_still_honors_sentinel_after_failure():
    """失败任务之后 sentinel 关停语义不变（bounded shutdown 契约）。"""
    def failing_job():
        raise ValueError("boom")

    assert bg.submit(failing_job)
    # shutdown 投递 sentinel 并限时 join；不应抛错或卡死
    bg.shutdown(timeout=2.0)
    worker = bg._worker
    assert worker is None or not worker.is_alive()
    # 关停后再次 submit 能重建写线程（惰性恢复契约不变）
    restarted = threading.Event()
    assert bg.submit(lambda: restarted.set())
    assert restarted.wait(timeout=5)


# ---------------------------------------------------------------------------
# 3. LibreOffice 转换：profile 隔离 + 超时整树终止 + reap
# ---------------------------------------------------------------------------


def _make_checker():
    return vc.WordVisualChecker(vision_gateway=None)


def test_docx_to_pdf_timeout_kills_tree_and_reaps(monkeypatch):
    checker = _make_checker()
    communicate_calls = []
    run_calls = []
    popen_cmds = []

    class _FakeProc:
        pid = 43210
        returncode = None

        def communicate(self, timeout=None):
            communicate_calls.append(timeout)
            if len(communicate_calls) == 1:
                raise subprocess.TimeoutExpired(cmd="soffice", timeout=timeout)
            return (b"", b"")

    monkeypatch.setattr(vc.shutil, "which", lambda name: "C:/fake/soffice.exe")
    monkeypatch.setattr(vc.subprocess, "Popen",
                        lambda cmd, **kwargs: popen_cmds.append(cmd)
                        or _FakeProc())
    monkeypatch.setattr(vc.subprocess, "run",
                        lambda cmd, **kwargs: run_calls.append(cmd)
                        or SimpleNamespace(returncode=0))

    import tempfile
    before = set(__import__("pathlib").Path(tempfile.gettempdir()).glob("lo-profile-*"))
    result = checker._docx_to_pdf("C:/fake/input.docx")
    after = set(__import__("pathlib").Path(tempfile.gettempdir()).glob("lo-profile-*"))

    assert result is None, "超时必须降级返回 None，不得假装成功"
    # 独立 profile：超时残留进程不会锁住共享默认 profile
    assert any("-env:UserInstallation=" in arg for arg in popen_cmds[0])
    # 第一次 communicate 超时后必须再次 communicate reap 直接子进程
    assert len(communicate_calls) == 2
    if __import__("os").name == "nt":
        # Windows 上 soffice.exe 只是启动器：必须整树 taskkill
        assert run_calls, "超时后必须终止进程树"
        assert run_calls[0][:3] == ["taskkill", "/F", "/T"]
        assert "43210" in run_calls[0]
    # 临时 profile 目录必须清理，不留垃圾
    assert after == before


def test_docx_to_pdf_nonzero_exit_logs_and_degrades(monkeypatch):
    checker = _make_checker()
    records = []
    handler = _ListHandler(records)
    vc.logger.addHandler(handler)

    class _FakeProc:
        pid = 1
        returncode = 3

        def communicate(self, timeout=None):
            return (b"", b"conversion exploded")

    monkeypatch.setattr(vc.shutil, "which", lambda name: "C:/fake/soffice.exe")
    monkeypatch.setattr(vc.subprocess, "Popen",
                        lambda cmd, **kwargs: _FakeProc())
    try:
        result = checker._docx_to_pdf("C:/fake/input.docx")
    finally:
        vc.logger.removeHandler(handler)

    assert result is None
    assert any("转换失败" in record.getMessage() for record in records), (
        "LibreOffice 转换失败必须留痕，不得静默吞掉"
    )
