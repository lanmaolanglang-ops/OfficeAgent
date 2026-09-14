"""P2-14：Windows 服务提权引用与 SvcDoRun/SvcStop 生命周期回归。

修复前：
- ``run_as_admin`` 用 ``" ".join(sys.argv)`` 拼接参数——含空格/引号/
  末尾反斜杠的路径（如 ``--app-dir "C:\\Program Files\\App\\"``）在提权
  后的进程里被重新切分成错误参数；
- 生成的 service_wrapper：SvcStop 先于后端就绪时（``_mgr`` 为 None）
  后端无人清理 → 孤儿进程；``start()`` 失败以未处理异常结束、无
  SERVICE_STOPPED 收口；清理失败可能把 SCM 停止流程拖挂。

测试直接 exec 生成的包装脚本（真实模板代码），win32/servicemanager 以
假模块注入，不弹 UAC、不安装服务。
"""
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import office_agent.runtime_config as runtime_config_module
import office_agent.runtime_manager as runtime_manager_module
from desktop.service_manager import (
    build_service_wrapper_script,
    elevated_command_line,
    run_as_admin,
)

WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
SERVICE_STOP_PENDING = 3
SERVICE_STOPPED = 1


# ============================================================
# 提权命令行引用
# ============================================================

class TestElevatedCommandLine:
    def test_plain_args(self):
        assert elevated_command_line(["s.py", "install"]) == "s.py install"

    def test_space_path_is_quoted(self):
        result = elevated_command_line(
            ["s.py", "--app-dir", r"C:\Program Files\App"])
        assert result == r's.py --app-dir "C:\Program Files\App"'

    def test_empty_argument(self):
        assert elevated_command_line(["s.py", ""]) == 's.py ""'

    def test_trailing_backslash_without_space_stays_unquoted(self):
        # list2cmdline 规则：无空格的参数不加引号，反斜杠原样保留
        assert elevated_command_line(["s.py", "C:\\dir\\"]) == "s.py C:\\dir\\"

    def test_trailing_backslash_with_space_is_escaped(self):
        # 真正触发转义：带引号的参数在闭合引号前反斜杠加倍
        assert elevated_command_line(["s.py", "C:\\dir with space\\"]) == \
            's.py "C:\\dir with space\\\\"'

    def test_embedded_quote_escaped(self):
        assert elevated_command_line(["s.py", 'a"b']) == 's.py a\\"b'

    def test_chinese_path_preserved(self):
        assert elevated_command_line(["s.py", "C:\\办公助手\\App"]) == \
            "s.py C:\\办公助手\\App"

    def test_key_value_ampersand_untouched(self):
        result = elevated_command_line(["s.py", "--key=value", "a&b"])
        assert result == "s.py --key=value a&b"

    def test_matches_stdlib_rule(self):
        """实现必须与 stdlib 的 Windows 引用规则一致，不得自造不完整算法。"""
        cases = [["a", "b c"], ["a", ""], ["a", r"x\""], ["a", 'q"q'],
                 ["a", "--k=v"], ["中文", r"C:\Program Files\X"]]
        for case in cases:
            assert elevated_command_line(case) == subprocess.list2cmdline(case)

    def test_run_as_admin_uses_quoted_parameters(self, monkeypatch):
        captured = {}

        def fake_shell_execute(hwnd, operation, file, parameters, directory,
                               show):
            captured.update(file=file, parameters=parameters,
                            operation=operation)

        import ctypes
        monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW",
                            fake_shell_execute)
        monkeypatch.setattr(sys, "argv",
                            ["service_manager.py", "install",
                             "--app-dir", r"C:\Program Files\App"])
        with pytest.raises(SystemExit):
            run_as_admin()
        assert captured["operation"] == "runas"
        assert captured["file"] == sys.executable
        assert captured["parameters"] == \
            r'service_manager.py install --app-dir "C:\Program Files\App"'


# ============================================================
# 生成的服务包装脚本生命周期（exec 真实模板代码）
# ============================================================

class _FakeEventImpl:
    def __init__(self, signals: list, release: threading.Event):
        self.signals = list(signals)
        self.release = release

    def CreateEvent(self, *args):
        return object()

    def SetEvent(self, event):
        self.release.set()

    def WaitForSingleObject(self, event, milliseconds):
        value = self.signals.pop(0) if self.signals else WAIT_OBJECT_0
        if value == WAIT_TIMEOUT:
            # 模拟阻塞等待：超时窗口内等到真实停止信号即返回 WAIT_OBJECT_0
            return WAIT_OBJECT_0 if self.release.wait(
                timeout=milliseconds / 1000) else WAIT_TIMEOUT
        return value


@pytest.fixture
def service_env(tmp_path, monkeypatch):
    """注入 win32/servicemanager/runtime_manager 假模块并 exec 生成脚本。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    previous_cwd = os.getcwd()

    release = threading.Event()
    event_impl = _FakeEventImpl([], release)
    statuses: list = []
    event_log: list = []
    runtime = SimpleNamespace(start_calls=0, stop_calls=0,
                              fail_start=False, fail_stop=False)

    sys.modules["win32service"] = SimpleNamespace(
        SERVICE_STOP_PENDING=SERVICE_STOP_PENDING,
        SERVICE_STOPPED=SERVICE_STOPPED, WAIT_OBJECT_0=WAIT_OBJECT_0)
    sys.modules["win32event"] = SimpleNamespace(
        WAIT_OBJECT_0=WAIT_OBJECT_0, WAIT_TIMEOUT=WAIT_TIMEOUT,
        CreateEvent=event_impl.CreateEvent, SetEvent=event_impl.SetEvent,
        WaitForSingleObject=event_impl.WaitForSingleObject)
    sys.modules["servicemanager"] = SimpleNamespace(
        EVENTLOG_INFORMATION_TYPE=4, PYS_SERVICE_STARTED=0x03000004,
        LogMsg=lambda *args: event_log.append(args),
        LogErrorMsg=lambda message: event_log.append(("error", message)))
    sys.modules["win32serviceutil"] = SimpleNamespace(
        ServiceFramework=type("ServiceFramework", (), {
            "__init__": lambda self, args: None}),
        HandleCommandLine=lambda cls: None)

    class _FakeManager:
        def __init__(self, config):
            self.config = config

        def start(self):
            runtime.start_calls += 1
            if runtime.fail_start:
                raise RuntimeError("backend boom")

        def stop(self):
            runtime.stop_calls += 1
            if runtime.fail_stop:
                raise RuntimeError("stop boom")

    monkeypatch.setattr(runtime_config_module,
                        "apply_desktop_runtime_env", lambda *_: None)
    monkeypatch.setattr(runtime_manager_module, "AppConfig",
                        lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runtime_manager_module, "ApplicationRuntimeManager",
                        _FakeManager)

    script = build_service_wrapper_script(app_dir, 8765, tmp_path / "data")
    namespace: dict = {"__name__": "service_wrapper_under_test"}
    exec(compile(script, "service_wrapper.py", "exec"), namespace)
    service_class = namespace["OfficeAgentService"]

    def make_service():
        svc = service_class(["wrapper"])
        svc.ReportServiceStatus = statuses.append
        return svc

    env = SimpleNamespace(make_service=make_service, event_impl=event_impl,
                          release=release, statuses=statuses,
                          event_log=event_log, runtime=runtime)
    try:
        yield env
    finally:
        for name in ("win32service", "win32event", "servicemanager",
                     "win32serviceutil"):
            sys.modules.pop(name, None)
        os.chdir(previous_cwd)


class TestServiceLifecycle:
    def test_normal_start_then_stop(self, service_env):
        """启动 → 收到停止信号 → 循环及时退出，后端清理恰好一次。"""
        service_env.event_impl.signals.append(WAIT_TIMEOUT)
        svc = service_env.make_service()
        thread = threading.Thread(target=svc.SvcDoRun, daemon=True)
        thread.start()
        deadline = time.time() + 5
        while service_env.runtime.start_calls < 1 and time.time() < deadline:
            time.sleep(0.01)
        svc.SvcStop()
        thread.join(timeout=5)
        assert not thread.is_alive(), "停止信号必须让主循环及时退出"
        assert service_env.runtime.start_calls == 1
        assert service_env.runtime.stop_calls == 1, "后端必须清理恰好一次"
        assert service_env.statuses == [SERVICE_STOP_PENDING, SERVICE_STOPPED]

    def test_stop_before_backend_ready_still_cleans_up(self, service_env):
        """回归核心：SvcStop 先于就绪时（_mgr 为 None），SvcDoRun 的
        finally 必须兜底清理，后端绝不成为孤儿。"""
        service_env.event_impl.signals.append(WAIT_OBJECT_0)  # 事件已置位
        svc = service_env.make_service()
        svc.SvcStop()  # 此时 _mgr 尚为 None
        assert service_env.runtime.stop_calls == 0
        svc.SvcDoRun()
        assert service_env.runtime.start_calls == 1
        assert service_env.runtime.stop_calls == 1, \
            "晚就绪的后端必须被退出路径兜底清理（旧实现留下孤儿进程）"

    def test_start_failure_reports_stopped_and_cleans_up(self, service_env):
        """启动失败：记录事件日志、清理半启动状态、明确上报 STOPPED。"""
        service_env.runtime.fail_start = True
        svc = service_env.make_service()
        svc.SvcDoRun()  # 不得向外抛未处理异常
        assert service_env.runtime.start_calls == 1
        assert service_env.runtime.stop_calls == 1
        assert service_env.statuses == [SERVICE_STOPPED]
        assert any(
            isinstance(entry, tuple) and entry[0] == "error"
            and "启动失败" in entry[1] and "backend boom" in entry[1]
            for entry in service_env.event_log)

    def test_cleanup_failure_does_not_hang_exit(self, service_env):
        """清理失败只记录事件日志，服务照常退出、状态照常上报。"""
        service_env.runtime.fail_stop = True
        svc = service_env.make_service()
        started = time.time()
        svc.SvcDoRun()
        assert time.time() - started < 5, "清理失败不得阻塞服务退出"
        assert service_env.statuses == [SERVICE_STOPPED]
        assert any(
            isinstance(entry, tuple) and entry[0] == "error"
            and "停止失败" in entry[1] for entry in service_env.event_log)

    def test_repeated_stop_is_idempotent(self, service_env):
        service_env.event_impl.signals.append(WAIT_TIMEOUT)
        svc = service_env.make_service()
        thread = threading.Thread(target=svc.SvcDoRun, daemon=True)
        thread.start()
        deadline = time.time() + 5
        while service_env.runtime.start_calls < 1 and time.time() < deadline:
            time.sleep(0.01)
        svc.SvcStop()
        svc.SvcStop()
        svc.SvcStop()
        thread.join(timeout=5)
        assert service_env.runtime.stop_calls == 1, "重复 SvcStop 只清理一次"

    def test_wait_blocks_instead_of_busy_spin(self, service_env):
        """等待是阻塞的 WaitForSingleObject（超时才重查），不是忙轮询。"""
        service_env.event_impl.signals.append(WAIT_TIMEOUT)
        svc = service_env.make_service()
        thread = threading.Thread(target=svc.SvcDoRun, daemon=True)
        thread.start()
        deadline = time.time() + 5
        while service_env.runtime.start_calls < 1 and time.time() < deadline:
            time.sleep(0.01)
        # 停止信号在等待分片过半后才发出：若实现是 busy spin，
        # 主循环会在 5s 超时前多次空转返回 WAIT_TIMEOUT。
        threading.Timer(0.3, svc.SvcStop).start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert service_env.runtime.stop_calls == 1
        assert service_env.statuses == [SERVICE_STOP_PENDING, SERVICE_STOPPED]


class TestGeneratedScriptShape:
    def test_script_contains_service_metadata(self, tmp_path):
        script = build_service_wrapper_script(tmp_path, 9000, tmp_path)
        # repr() 嵌入服务名：单引号或双引号均可
        assert "OfficeAgent" in script
        assert "port=9000" in script
        assert "WaitForSingleObject" in script

    def test_handle_command_line_only_under_main_guard(self, tmp_path):
        script = build_service_wrapper_script(tmp_path, 9000, tmp_path)
        assert 'if __name__ == "__main__":' in script
