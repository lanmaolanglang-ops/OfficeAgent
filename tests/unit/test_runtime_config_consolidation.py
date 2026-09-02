import importlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path


def test_data_root_drives_storage_config_knowledge_and_task_outputs(tmp_path, monkeypatch):
    data_root = tmp_path / "runtime-data"
    monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(data_root))

    from office_agent.api.core.config import APIConfig
    from office_agent.config_system.loaders import YamlLoader
    from office_agent.config_system.schemas import LoggingConfig, StorageConfig as SchemaStorage
    from office_agent.knowledge_base.knowledge_base import _default_kb_storage_dir
    from office_agent.storage.storage_service import StorageConfig
    from office_agent.task_queue.tasks import excel_tasks, ppt_tasks, word_tasks

    assert StorageConfig().local_path == str(data_root / "storage")
    assert SchemaStorage().local_path == str(data_root / "storage")
    assert LoggingConfig().dir == str(data_root / "logs")
    assert APIConfig().upload_dir == str(data_root / "uploads")
    assert APIConfig().output_dir == str(data_root / "outputs")
    assert YamlLoader().config_dir == data_root / "config"
    assert _default_kb_storage_dir() == str(data_root / "kb_data")

    assert Path(word_tasks._safe_output()).parent == data_root / "outputs"
    assert Path(excel_tasks._safe_output()).parent == data_root / "outputs"
    assert Path(ppt_tasks._safe_filename("ignored")).parent == data_root / "outputs"

    explicit_output = tmp_path / "operator-output"
    monkeypatch.setenv("OFFICE_AGENT_OUTPUT_DIR", str(explicit_output))
    assert Path(word_tasks._safe_output()).parent == explicit_output
    assert APIConfig().output_dir == str(explicit_output)


def test_log_dir_inherits_data_root_and_honors_explicit_override(tmp_path, monkeypatch):
    from office_agent.api.core.config import APIConfig
    from office_agent.config_system.schemas import LoggingConfig
    from office_agent.runtime_config import get_log_dir

    monkeypatch.delenv("LOG_DIR", raising=False)
    monkeypatch.delenv("OFFICE_AGENT_LOG_DIR", raising=False)
    monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path / "data"))
    assert get_log_dir() == tmp_path / "data" / "logs"

    monkeypatch.setenv("OFFICE_AGENT_LOG_DIR", str(tmp_path / "service-logs"))
    assert get_log_dir() == tmp_path / "service-logs"
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "operator-logs"))
    assert get_log_dir() == tmp_path / "operator-logs"
    assert APIConfig().log_dir == str(tmp_path / "operator-logs")
    assert LoggingConfig().dir == str(tmp_path / "operator-logs")


def test_desktop_root_is_shared_by_launcher_runtime_and_explicit_override(tmp_path, monkeypatch):
    import office_agent.runtime_config as runtime_config
    import office_agent.runtime_manager as runtime_manager

    monkeypatch.delenv("OFFICE_AGENT_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(runtime_config.sys, "platform", "win32")
    expected = tmp_path / "appdata" / "OfficeAgent"
    assert runtime_config.get_desktop_data_root() == expected

    monkeypatch.setattr(runtime_manager, "get_desktop_data_root", lambda: expected)
    assert runtime_manager.AppConfig().data_dir == expected

    explicit = tmp_path / "portable"
    monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(explicit))
    assert runtime_config.get_desktop_data_root() == explicit


def test_release_version_has_one_python_source():
    root = Path(__file__).parents[2]
    assignments = []
    pattern = re.compile(r'^__version__\s*=\s*["\']', re.MULTILINE)
    for path in (root / "office_agent").rglob("*.py"):
        if pattern.search(path.read_text(encoding="utf-8")):
            assignments.append(path.relative_to(root).as_posix())
    assert sorted(assignments) == ["office_agent/_version.py"]

    with (root / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    assert config["project"]["dynamic"] == ["version"]
    assert config["tool"]["setuptools"]["dynamic"]["version"]["attr"] == (
        "office_agent._version.__version__"
    )

    office_agent = importlib.import_module("office_agent")
    runtime_manager = importlib.import_module("office_agent.runtime_manager")
    assert office_agent.__version__ == runtime_manager.AppConfig().app_version


def test_package_version_import_does_not_eagerly_load_business_subsystems():
    code = (
        "import sys; import office_agent; "
        "assert 'office_agent.api' not in sys.modules; "
        "assert 'office_agent.model_gateway' not in sys.modules; "
        "assert office_agent.__version__"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_lazy_root_exports_remain_backwards_compatible():
    import office_agent
    from office_agent.model_gateway import ModelGateway
    from office_agent.services import WordService

    assert office_agent.ModelGateway is ModelGateway
    assert office_agent.WordService is WordService


def test_placeholder_font_prefers_operator_config_then_platform_candidates(monkeypatch):
    from PIL import ImageFont
    from office_agent.vision_gateway import document_renderer

    requested = []
    selected = object()

    def fake_truetype(candidate, size):
        requested.append((candidate, size))
        if candidate == "/fonts/company-cjk.ttf":
            return selected
        raise OSError("font unavailable")

    document_renderer._load_placeholder_font.cache_clear()
    monkeypatch.setenv("OFFICE_AGENT_FONT", "/fonts/company-cjk.ttf")
    monkeypatch.setattr(ImageFont, "truetype", fake_truetype)
    try:
        assert document_renderer._load_placeholder_font(18) is selected
        assert requested == [("/fonts/company-cjk.ttf", 18)]
    finally:
        document_renderer._load_placeholder_font.cache_clear()


def test_placeholder_font_falls_back_without_crashing(monkeypatch):
    from PIL import ImageFont
    from office_agent.vision_gateway import document_renderer

    fallback = object()
    document_renderer._load_placeholder_font.cache_clear()
    monkeypatch.delenv("OFFICE_AGENT_FONT", raising=False)
    monkeypatch.setattr(ImageFont, "truetype", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(ImageFont, "load_default", lambda: fallback)
    try:
        assert document_renderer._load_placeholder_font(12) is fallback
    finally:
        document_renderer._load_placeholder_font.cache_clear()
