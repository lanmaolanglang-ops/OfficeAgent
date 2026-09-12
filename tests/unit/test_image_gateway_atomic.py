"""P3-13：图像网关异常分类与原子落盘回归。

修复前：
- ``_post_json`` 末行的 ``json.loads(body)`` 对非 JSON 响应（HTML 错误
  页、截断响应）裸抛 JSONDecodeError，非法编码裸抛 UnicodeDecodeError，
  均未归类为 ImageGenerationError、丢失上下文；
- MCP 返回体 ``result`` 为 null 时 ``None.get("content")`` 裸抛
  AttributeError；
- 生图文件与配置均直接 ``write``/``json.dump`` 到最终路径——进程中断
  留下半张图片/半截配置冒充成品。生图文件改用项目统一原子写
  （同目录临时文件 + fsync + os.replace），配置亦然。
"""
import base64
import json
import os

import pytest

import office_agent.image_generation.gateway as gateway_module
from office_agent.image_generation.config import ImageModelConfigManager
from office_agent.image_generation.gateway import (
    ImageGenerationError,
    ImageGenerationGateway,
)


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestPostJsonClassification:
    def test_non_json_body_classified(self, monkeypatch):
        """HTML/截断响应归类为 ImageGenerationError 并保留 cause。"""
        monkeypatch.setattr(
            gateway_module, "urlopen",
            lambda req, timeout: _FakeResponse(
                b"<html>Service Unavailable</html>"))
        with pytest.raises(ImageGenerationError, match="非 JSON 响应") as excinfo:
            gateway_module._post_json("https://x", {}, {})
        assert isinstance(excinfo.value.__cause__, ValueError)

    def test_invalid_utf8_body_classified(self, monkeypatch):
        monkeypatch.setattr(
            gateway_module, "urlopen",
            lambda req, timeout: _FakeResponse(b"\xff\xfe\x00\x00broken"))
        with pytest.raises(ImageGenerationError, match="UTF-8"):
            gateway_module._post_json("https://x", {}, {})

    def test_valid_json_still_parses(self, monkeypatch):
        monkeypatch.setattr(
            gateway_module, "urlopen",
            lambda req, timeout: _FakeResponse(b'{"data": [1, 2]}'))
        assert gateway_module._post_json("https://x", {}, {}) == {"data": [1, 2]}


class TestMcpNoneHandling:
    def _gateway(self):
        return ImageGenerationGateway(
            provider="mcp", mcp_url="https://mcp.example.com")

    def test_null_result_is_classified_not_attribute_error(self, monkeypatch):
        """MCP ``result: null`` 必须归类为未返回图片，不得裸 AttributeError。"""
        gateway = self._gateway()
        monkeypatch.setattr(
            gateway_module, "urlopen",
            lambda req, timeout: _FakeResponse(
                json.dumps({"result": None}).encode("utf-8")))
        with pytest.raises(ImageGenerationError, match="MCP 图像工具未返回图片"):
            gateway._generate_mcp("prompt", "1024x768", None)

    def test_missing_result_key_falls_back_to_payload(self, monkeypatch, tmp_path):
        """无 result 包裹、直接给 b64 的 MCP 形态仍正常工作。"""
        gateway = self._gateway()
        payload = json.dumps(
            {"b64_json": base64.b64encode(b"png").decode()})
        monkeypatch.setattr(gateway_module, "urlopen",
                            lambda req, timeout: _FakeResponse(payload.encode()))
        out = tmp_path / "out"
        path = gateway._generate_mcp("prompt", "1024x768", str(out))
        try:
            assert open(path, "rb").read() == b"png"
        finally:
            if os.path.exists(path):
                os.remove(path)


class TestAtomicImageSave:
    def test_generate_writes_atomically_and_completes(self, monkeypatch, tmp_path):
        """生图落盘走统一原子写：成功后文件完整、目录无 .tmp 残留。"""
        gateway = ImageGenerationGateway(provider="agnes", api_key="key")
        payload = json.dumps({"data": [{
            "b64_json": base64.b64encode(b"fake-png-bytes").decode()}]})
        monkeypatch.setattr(gateway_module, "urlopen",
                            lambda req, timeout: _FakeResponse(payload.encode()))
        out = tmp_path / "images"
        path = gateway.generate("prompt", output_dir=str(out))
        try:
            assert open(path, "rb").read() == b"fake-png-bytes"
            residue = [f for f in os.listdir(out) if ".tmp-" in f]
            assert residue == [], "成功后不得残留临时文件"
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_write_failure_leaves_no_partial_file(self, monkeypatch, tmp_path):
        """落盘失败不得留下半截图片冒充成品。"""
        gateway = ImageGenerationGateway(provider="agnes", api_key="key")
        payload = json.dumps({"data": [{
            "b64_json": base64.b64encode(b"fake-png-bytes").decode()}]})
        monkeypatch.setattr(gateway_module, "urlopen",
                            lambda req, timeout: _FakeResponse(payload.encode()))
        monkeypatch.setattr(gateway_module, "atomic_write_bytes",
                            lambda path, data: (_ for _ in ()).throw(
                                OSError("disk full")))
        out = tmp_path / "images"
        with pytest.raises(OSError, match="disk full"):
            gateway.generate("prompt", output_dir=str(out))
        assert list(out.iterdir()) == [], "失败路径不得残留任何文件"


class TestAtomicConfigSave:
    def test_save_config_writes_readable_json_without_plaintext_key(
            self, tmp_path):
        manager = ImageModelConfigManager(config_dir=str(tmp_path))
        manager.save_config(provider="agnes", api_key="sk-test",
                            base_url="https://x", model="m")
        data = json.loads((tmp_path / "image_model.json").read_text("utf-8"))
        assert data["provider"] == "agnes"
        assert "sk-test" not in json.dumps(data), "明文 Key 不得落盘"

    def test_save_failure_keeps_previous_config(self, tmp_path, monkeypatch):
        """原子写失败时磁盘上保留上一份完整配置（不得留下半截 JSON）。"""
        import office_agent.image_generation.config as config_module
        manager = ImageModelConfigManager(config_dir=str(tmp_path))
        manager.save_config(provider="agnes", api_key="sk-old",
                            base_url="https://old", model="old-model")
        before = (tmp_path / "image_model.json").read_text("utf-8")

        def failing_write(path, data, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(config_module, "atomic_write_json", failing_write)
        with pytest.raises(OSError):
            manager.save_config(provider="openai", api_key="sk-new",
                                base_url="https://new", model="new-model")
        assert (tmp_path / "image_model.json").read_text("utf-8") == before, \
            "写失败后旧配置必须完整保留"
