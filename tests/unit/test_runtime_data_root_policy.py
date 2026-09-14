"""P5-14：应用数据目录单一来源（canonical runtime location）回归。

修复前：``get_data_root()``（backend 默认 ``~/.office_agent``）与
``get_desktop_data_root()``（desktop 默认 ``%APPDATA%/OfficeAgent``）是两套
互相独立的默认值，同一个 OfficeAgent 会因入口不同读写不同根目录——直接
运行 backend（未设 ``OFFICE_AGENT_DATA_DIR``）会落到 home 目录，而桌面
安装版的数据在 ``%APPDATA%/OfficeAgent``，表现为"配置写在 A、DB 读自 B"
或"看起来丢数据"。

修复后：两个入口共用唯一 resolver ``resolve_data_root()``，优先级
显式 env → frozen/安装版平台原生根 → 源码/开发版 ``~/.office_agent``，
且从不依赖 cwd / 项目根，重启与换工作目录都不漂移。

测试全部用 monkeypatch 的临时 HOME/APPDATA，绝不读写真实用户目录。
"""
import importlib
import subprocess
import sys
from pathlib import Path

import office_agent.runtime_config as runtime_config


def _clear_data_env(monkeypatch):
    for key in ("OFFICE_AGENT_DATA_DIR", "OFFICE_AGENT_LOG_DIR", "LOG_DIR",
                "OFFICE_AGENT_OUTPUT_DIR", "OFFICE_AGENT_UPLOAD_DIR"):
        monkeypatch.delenv(key, raising=False)


def test_explicit_env_override_wins_for_every_entry_point(tmp_path, monkeypatch):
    explicit = tmp_path / "portable-data"
    monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(explicit))

    assert runtime_config.resolve_data_root() == explicit
    assert runtime_config.get_data_root() == explicit
    assert runtime_config.get_desktop_data_root() == explicit
    # 日志/输出/上传默认派生自同一根
    assert runtime_config.get_log_dir() == explicit / "logs"
    assert runtime_config.get_output_dir() == explicit / "outputs"
    assert runtime_config.get_upload_dir() == explicit / "uploads"


def test_frozen_installed_build_uses_platform_native_root(tmp_path, monkeypatch):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(runtime_config.sys, "platform", "win32")
    monkeypatch.setattr(runtime_config.sys, "frozen", True, raising=False)

    expected = tmp_path / "appdata" / "OfficeAgent"
    assert runtime_config.resolve_data_root() == expected
    # backend 与 desktop 必须给出同一个根（修复前 backend 会给 ~/.office_agent）
    assert runtime_config.get_data_root() == expected
    assert runtime_config.get_desktop_data_root() == expected
    assert runtime_config.get_log_dir() == expected / "logs"


def test_source_run_uses_development_root_not_installed_root(tmp_path, monkeypatch):
    _clear_data_env(monkeypatch)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    # Windows 的 Path.home() 读 USERPROFILE 而非 HOME，直接钉住 Path.home
    monkeypatch.setattr(runtime_config.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(runtime_config.sys, "platform", "win32")
    monkeypatch.setattr(runtime_config.sys, "frozen", False, raising=False)

    expected = fake_home / ".office_agent"
    assert runtime_config.resolve_data_root() == expected
    # 开发运行绝不指向安装版的 %APPDATA%\OfficeAgent
    assert runtime_config.get_data_root() == expected
    assert runtime_config.get_desktop_data_root() == expected


def test_backend_and_desktop_never_disagree(tmp_path, monkeypatch):
    """三种模式（override / frozen / source）下两个入口恒等。"""
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runtime_config.sys, "platform", "win32")

    for frozen in (True, False):
        monkeypatch.setattr(runtime_config.sys, "frozen", frozen, raising=False)
        assert runtime_config.get_data_root() == runtime_config.get_desktop_data_root()

    monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path / "explicit"))
    monkeypatch.setattr(runtime_config.sys, "frozen", False, raising=False)
    assert runtime_config.get_data_root() == runtime_config.get_desktop_data_root()


def test_resolver_is_cwd_independent_and_stable_across_restarts(tmp_path, monkeypatch):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(runtime_config.sys, "platform", "win32")
    monkeypatch.setattr(runtime_config.sys, "frozen", True, raising=False)

    first = runtime_config.resolve_data_root()
    other = tmp_path / "somewhere-else"
    other.mkdir()
    monkeypatch.chdir(other)
    second = runtime_config.resolve_data_root()
    monkeypatch.chdir(tmp_path)
    third = runtime_config.resolve_data_root()
    assert first == second == third


def test_log_dir_override_still_takes_precedence(tmp_path, monkeypatch):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path / "data"))
    assert runtime_config.get_log_dir() == tmp_path / "data" / "logs"
    monkeypatch.setenv("OFFICE_AGENT_LOG_DIR", str(tmp_path / "service-logs"))
    assert runtime_config.get_log_dir() == tmp_path / "service-logs"
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "operator-logs"))
    # P3-3: namespaced override takes precedence over the generic shared LOG_DIR.
    assert runtime_config.get_log_dir() == tmp_path / "service-logs"


def test_all_derived_paths_share_the_one_resolved_root(tmp_path):
    """子进程验证：DB / storage / config / logs / outputs / uploads / kb
    全部落在显式 override 之下，没有任何模块自行猜 home 或项目根。"""
    data_root = tmp_path / "runtime-root"
    code = (
        "import json;"
        "from office_agent.database.connection import DATA_DIR, DEFAULT_DB_PATH;"
        "from office_agent.config_system.loaders import YamlLoader;"
        "from office_agent.config_system.schemas import LoggingConfig, StorageConfig;"
        "from office_agent.api.core.config import APIConfig;"
        "from office_agent.knowledge_base.knowledge_base import _default_kb_storage_dir;"
        "from office_agent.knowledge_base.ann_index import get_default_ann_index_dir;"
        "print(json.dumps({"
        "'db': str(DATA_DIR), 'db_file': str(DEFAULT_DB_PATH),"
        "'storage': StorageConfig().local_path,"
        "'config': str(YamlLoader().config_dir),"
        "'logs': LoggingConfig().dir,"
        "'uploads': APIConfig().upload_dir,"
        "'outputs': APIConfig().output_dir,"
        "'kb': _default_kb_storage_dir(),"
        "'rag': str(get_default_ann_index_dir()),"
        "}))"
    )
    import json
    import os
    env = dict(os.environ)
    env["OFFICE_AGENT_DATA_DIR"] = str(data_root)
    for key in ("OFFICE_AGENT_LOG_DIR", "LOG_DIR", "OFFICE_AGENT_OUTPUT_DIR",
                "OFFICE_AGENT_UPLOAD_DIR", "DATABASE_URL"):
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[2], capture_output=True, text=True,
        env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    paths = json.loads(result.stdout.strip().splitlines()[-1])
    root = str(data_root)
    for name, value in paths.items():
        assert value.startswith(root), f"{name} 未落在 canonical 根下: {value}"


def test_resolver_never_guesses_cwd_or_project_root():
    """源码守卫：resolver 不得依赖 cwd / __file__ 推导数据根。"""
    import inspect
    source = inspect.getsource(runtime_config.resolve_data_root)
    for forbidden in ("cwd", "getcwd", "__file__", "chdir"):
        assert forbidden not in source, \
            f"resolve_data_root 不得使用 {forbidden} 推导数据根"


def test_desktop_and_backend_import_the_same_resolver():
    """多入口守卫：backend / desktop 均经同一 resolver，不得各自写默认值。"""
    import inspect

    import desktop.app_launcher as app_launcher
    import office_agent.runtime_manager as runtime_manager

    assert "get_desktop_data_root" in inspect.getsource(app_launcher.main)
    assert "get_desktop_data_root" in inspect.getsource(
        runtime_manager.AppConfig.__post_init__)
    # 两个入口函数体都不得再自行拼 ~/.office_agent 或 APPDATA
    for func in (runtime_config.get_data_root, runtime_config.get_desktop_data_root):
        body = inspect.getsource(func)
        assert "Path.home()" not in body
        assert "APPDATA" not in body


def test_importlib_reload_keeps_the_same_root(tmp_path, monkeypatch):
    """重启等价：重新导入 runtime_config 后解析结果不变。"""
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(runtime_config.sys, "platform", "win32")
    monkeypatch.setattr(runtime_config.sys, "frozen", True, raising=False)

    before = runtime_config.resolve_data_root()
    reloaded = importlib.reload(runtime_config)
    try:
        assert reloaded.resolve_data_root() == before
    finally:
        importlib.reload(runtime_config)
