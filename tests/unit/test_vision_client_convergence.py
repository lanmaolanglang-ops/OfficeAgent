"""视觉客户端收敛专项测试。

model_gateway.clients.*.analyze_image 是唯一兼容外观，
真实实现必须委托 vision_gateway.clients 中对应提供商的视觉客户端；
旧入口（import path、ModelResponse 返回类型、超时与大小限制）保持兼容。
"""
import inspect

import pytest

from office_agent.models.model_schemas import ModelConfig, ModelProvider, ModelResponse
from office_agent.model_gateway.clients.base import BaseModelClient
from office_agent.model_gateway.clients.openai_client import OpenAIClient
from office_agent.model_gateway.clients.gemini_client import GeminiClient
from office_agent.model_gateway.clients.claude_client import ClaudeClient
from office_agent.model_gateway.clients.doubao_client import DoubaoClient
from office_agent.vision_gateway.clients.openai_client import OpenAIVisionClient
from office_agent.vision_gateway.clients.gemini_client import GeminiVisionClient
from office_agent.vision_gateway.clients.claude_client import ClaudeVisionClient
from office_agent.vision_gateway.vision_models import (
    ImageSource, VisionRequest, VisionResponse,
)


def _config(provider=ModelProvider.OPENAI, **overrides):
    params = dict(
        id="m1", provider=provider, display_name="测试模型",
        api_key="key", base_url="https://api.example.com/v1",
        model="vision-model", supports_vision=True,
        max_tokens=1234, temperature=0.7, timeout=42,
    )
    params.update(overrides)
    return ModelConfig(**params)


@pytest.fixture()
def image_file(tmp_path):
    path = tmp_path / "pic.png"
    path.write_bytes(b"\x89PNGfake")
    return str(path)


def _capture_analyze(monkeypatch, vision_cls):
    captured = {}

    def fake_analyze(self, request):
        captured["request"] = request
        captured["timeout"] = self.timeout
        captured["api_key"] = self.api_key
        captured["model"] = self.model
        captured["base_url"] = self.base_url
        return VisionResponse(
            success=True, content="分析结果", model_used=self.model,
            provider=self.provider_name, tokens_used=99, latency_ms=12,
        )

    monkeypatch.setattr(vision_cls, "analyze", fake_analyze)
    return captured


# ---------------------------------------------------------------
# 委托与适配
# ---------------------------------------------------------------

def test_openai_analyze_image_delegates_to_vision_gateway(monkeypatch, image_file):
    captured = _capture_analyze(monkeypatch, OpenAIVisionClient)
    resp = OpenAIClient(_config()).analyze_image(
        image_file, "看图说话", system_prompt="系统提示"
    )
    assert isinstance(resp, ModelResponse)
    assert resp.success and resp.content == "分析结果"
    assert resp.model_used == "m1"
    assert resp.tokens_used == 99
    assert resp.latency_ms == 12

    request = captured["request"]
    assert isinstance(request, VisionRequest)
    assert request.prompt == "看图说话"
    assert request.system_prompt == "系统提示"
    assert request.require_structured is False
    # 配置语义传递：生成参数、超时、凭据、模型与 base_url
    assert request.max_tokens == 1234
    assert request.temperature == 0.7
    assert captured["timeout"] == 42
    assert captured["api_key"] == "key"
    assert captured["model"] == "vision-model"
    assert captured["base_url"] == "https://api.example.com/v1"
    # 图片以本地文件形式传递
    assert len(request.images) == 1
    assert request.images[0].source == ImageSource.FILE
    assert request.images[0].path == image_file


def test_gemini_and_claude_delegate_to_their_vision_clients(monkeypatch, image_file):
    captured_g = _capture_analyze(monkeypatch, GeminiVisionClient)
    resp = GeminiClient(_config(provider=ModelProvider.GEMINI)).analyze_image(
        image_file, "p")
    assert resp.success and resp.provider == "gemini"
    assert captured_g["request"].prompt == "p"

    captured_c = _capture_analyze(monkeypatch, ClaudeVisionClient)
    resp = ClaudeClient(_config(provider=ModelProvider.CLAUDE)).analyze_image(
        image_file, "p")
    assert resp.success and resp.provider == "claude"
    assert captured_c["request"].prompt == "p"


def test_doubao_inherits_openai_compatible_delegation():
    assert DoubaoClient._vision_client_cls is OpenAIVisionClient


def test_vision_failure_maps_to_model_response(monkeypatch, image_file):
    def fake_analyze(self, request):
        return VisionResponse(success=False, error="上游拒绝", latency_ms=3)

    monkeypatch.setattr(OpenAIVisionClient, "analyze", fake_analyze)
    resp = OpenAIClient(_config()).analyze_image(image_file, "p")
    assert not resp.success
    assert resp.error == "上游拒绝"
    assert resp.latency_ms == 3


# ---------------------------------------------------------------
# 防御与兼容
# ---------------------------------------------------------------

def test_non_vision_model_rejected_without_delegation(monkeypatch, image_file):
    called = []
    monkeypatch.setattr(
        OpenAIVisionClient, "analyze",
        lambda self, request: called.append(request),
    )
    resp = OpenAIClient(_config(supports_vision=False)).analyze_image(image_file, "p")
    assert not resp.success
    assert "不支持图片分析" in resp.error
    assert called == []


def test_client_without_mapping_reports_unsupported(image_file):
    class _PlainClient(BaseModelClient):
        def chat(self, messages, system_prompt=None, **kwargs):
            return ModelResponse(success=True)

    resp = _PlainClient(_config()).analyze_image(image_file, "p")
    assert not resp.success
    assert "不支持图片分析" in resp.error


def test_oversized_image_rejected_before_delegation(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(
        OpenAIVisionClient, "analyze",
        lambda self, request: called.append(request),
    )
    image = tmp_path / "large.png"
    image.write_bytes(b"x" * 11)
    config = _config()
    config.extra_params["max_image_bytes"] = 10
    resp = OpenAIClient(config).analyze_image(str(image), "p")
    assert not resp.success
    assert "大小限制" in resp.error
    assert called == []


def test_missing_image_returns_error_not_exception(image_file):
    resp = OpenAIClient(_config()).analyze_image(
        image_file + ".missing", "p")
    assert not resp.success
    assert resp.error


def test_default_system_prompt_is_empty_string(monkeypatch, image_file):
    captured = _capture_analyze(monkeypatch, OpenAIVisionClient)
    OpenAIClient(_config()).analyze_image(image_file, "p")
    assert captured["request"].system_prompt == ""


# ---------------------------------------------------------------
# 唯一实现守卫：model_gateway 子类不得再自带视觉 HTTP 实现
# ---------------------------------------------------------------

def test_model_gateway_subclasses_have_no_own_analyze_image():
    for cls in (OpenAIClient, GeminiClient, ClaudeClient):
        assert "analyze_image" not in cls.__dict__, (
            f"{cls.__name__} 仍保留重复的视觉实现")
    assert "analyze_image" in BaseModelClient.__dict__


def test_delegation_source_uses_vision_gateway():
    source = inspect.getsource(BaseModelClient.analyze_image)
    assert "vision_gateway" in source
    assert "urlopen" not in source
