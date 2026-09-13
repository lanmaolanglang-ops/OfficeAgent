"""frozen backend 启动器与桌面 supervisor 的命令行契约专项测试。

desktop-client/src-tauri/src/lib.rs 的 spawn_backend 以如下命令行拉起 frozen
backend：

    OfficeAgent.exe --host 127.0.0.1 --port 8765 --background

该契约必须被 desktop/app_launcher.py 接受。历史上 launcher 没有定义
``--background``，argparse 在初始化日志之前就以退出码 2 终止，导致正式安装版
后端永远无法就绪（GUI 只剩空壳）。这些测试锁定参数解析，防止跨语言契约再次错配。
"""
import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LAUNCHER_PATH = _REPO_ROOT / "desktop" / "app_launcher.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location(
        "oa_app_launcher_under_test", _LAUNCHER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def launcher():
    return _load_launcher()


def test_parser_accepts_full_supervisor_command_line(launcher):
    # 与 src-tauri spawn_backend 实际下发的参数逐字一致。
    args = launcher.build_parser().parse_args(
        ["--host", "127.0.0.1", "--port", "8765", "--background"]
    )
    assert args.host == "127.0.0.1"
    assert args.port == 8765
    assert args.background is True


def test_background_flag_defaults_to_false(launcher):
    args = launcher.build_parser().parse_args([])
    assert args.background is False


def test_supervisor_command_line_does_not_exit(launcher):
    # 未知参数才会触发 SystemExit(2)；契约参数必须被平稳接受。
    parser = launcher.build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--no-such-flag"])
    assert exc_info.value.code == 2
    # 反向保证：真实命令行不抛 SystemExit。
    parser.parse_args(["--host", "127.0.0.1", "--port", "8765", "--background"])
