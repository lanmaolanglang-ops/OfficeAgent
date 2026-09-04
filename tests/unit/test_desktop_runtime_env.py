"""桌面运行时环境变量唯一来源专项测试。

OFFICE_AGENT_LOCAL/AUTH_MODE/OFFICE_AGENT_DATA_DIR/OFFICE_AGENT_LOG_DIR/
OFFICE_AGENT_VERSION 的唯一来源是 runtime_config.desktop_runtime_env；
app_launcher（frozen）、runtime_manager（开发模式子进程）与
service_manager（Windows 服务包装脚本）都必须复用，不得各自手写。
"""
import inspect
import os

from office_agent.runtime_config import (
    apply_desktop_runtime_env, desktop_runtime_env,
)


def test_desktop_runtime_env_mapping(tmp_path):
    env = desktop_runtime_env(tmp_path, log_dir=tmp_path / "logs",
                              app_version="1.2.3")
    assert env == {
        "OFFICE_AGENT_LOCAL": "1",
        "AUTH_MODE": "local",
        "OFFICE_AGENT_DATA_DIR": str(tmp_path),
        "OFFICE_AGENT_LOG_DIR": str(tmp_path / "logs"),
        "OFFICE_AGENT_VERSION": "1.2.3",
    }


def test_log_dir_defaults_under_data_dir(tmp_path):
    env = desktop_runtime_env(tmp_path)
    assert env["OFFICE_AGENT_LOG_DIR"] == str(tmp_path / "logs")


def test_version_omitted_when_not_given(tmp_path):
    env = desktop_runtime_env(tmp_path)
    assert "OFFICE_AGENT_VERSION" not in env


_RUNTIME_ENV_KEYS = ("OFFICE_AGENT_LOCAL", "AUTH_MODE", "OFFICE_AGENT_DATA_DIR",
                     "OFFICE_AGENT_LOG_DIR", "OFFICE_AGENT_VERSION")


def test_apply_updates_process_environ(tmp_path):
    # apply_* 直接写 os.environ（产品语义）；monkeypatch 无法追踪这种写入，
    # 显式保存/恢复，避免泄漏到后续测试。
    saved = {k: os.environ.get(k) for k in _RUNTIME_ENV_KEYS}
    try:
        for key in _RUNTIME_ENV_KEYS:
            os.environ.pop(key, None)
        applied = apply_desktop_runtime_env(tmp_path, app_version="9.9")
        assert os.environ["OFFICE_AGENT_DATA_DIR"] == str(tmp_path)
        assert os.environ["AUTH_MODE"] == "local"
        assert os.environ["OFFICE_AGENT_VERSION"] == "9.9"
        assert applied["OFFICE_AGENT_LOCAL"] == "1"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_runtime_manager_child_env_uses_shared_source(tmp_path, monkeypatch):
    monkeypatch.setenv("PRE_EXISTING", "keep")
    from office_agent.runtime_manager import AppConfig, ApplicationRuntimeManager

    config = AppConfig(data_dir=tmp_path / "data", backend_dir=tmp_path,
                       app_version="2.0")
    env = ApplicationRuntimeManager(config)._get_env()
    expected = desktop_runtime_env(config.data_dir, log_dir=config.log_dir,
                                   app_version="2.0")
    for key, value in expected.items():
        assert env[key] == value
    # 既有显式环境变量保留，backend_dir 仍注入 PYTHONPATH
    assert env["PRE_EXISTING"] == "keep"
    assert str(tmp_path) in env["PYTHONPATH"]


def test_frozen_launcher_uses_shared_source():
    import desktop.app_launcher as launcher

    source = inspect.getsource(launcher.run_uvicorn_direct)
    assert "apply_desktop_runtime_env" in source
    for var in ("OFFICE_AGENT_LOCAL", "AUTH_MODE", "OFFICE_AGENT_DATA_DIR",
                "OFFICE_AGENT_LOG_DIR", "OFFICE_AGENT_VERSION"):
        assert f'os.environ["{var}"]' not in source


def test_service_wrapper_script_uses_shared_source():
    import desktop.service_manager as service_manager

    source = inspect.getsource(service_manager.install_service)
    assert "apply_desktop_runtime_env" in source
    assert 'os.environ["OFFICE_AGENT_LOCAL"]' not in source


def test_runtime_manager_has_no_inline_env_block():
    from office_agent.runtime_manager import ApplicationRuntimeManager

    source = inspect.getsource(ApplicationRuntimeManager._get_env)
    assert "desktop_runtime_env" in source
    assert 'env["OFFICE_AGENT_LOCAL"]' not in source
