"""P1-9 回归测试：三家视觉供应商客户端的**上层语义**必须一致。

范围：``model_gateway/clients/{gemini,claude,doubao}_client.py``
（不是 ``vision_gateway/`` 那套）。

历史不一致：
  * 空响应：OpenAI/豆包返回 ``success=False, "模型返回空内容"``，
    Gemini/Claude 却返回 ``success=True, content=""``；
  * 超时：三家都落进通用 ``except Exception`` -> "请求失败: ..."，
    调用方无法区分可重试的超时；
  * usage 缺失、malformed 响应、HTTP 错误各自的容错口径不同。

目标不是让三份源码长得一样（厂商报文格式本就不同），
而是上层切换 provider 时拿到一致的成功/失败/超时/空响应语义。

全部使用 mock，禁止发起真实 Gemini / Claude / Doubao API 请求。
"""
import json
from urllib.error import HTTPError, URLError

import pytest

from office_agent.models.model_schemas import ModelConfig, ModelProvider
from office_agent.model_gateway.clients.claude_client import ClaudeClient
from office_agent.model_gateway.clients.doubao_client import DoubaoClient
from office_agent.model_gateway.clients.gemini_client import GeminiClient
from office_agent.model_gateway.clients.openai_client import OpenAIClient


def _config(provider=ModelProvider.OPENAI, model="m", base_url="https://x/v1"):
    return ModelConfig(
        id=f"{provider.value}-default",
        provider=provider,
        display_name=provider.value,
        api_key="sk-test",
        base_url=base_url,
        model=model,
        timeout=5,
    )


CLIENTS = [
    ("openai", lambda: OpenAIClient(_config(ModelProvider.OPENAI))),
    ("gemini", lambda: GeminiClient(_config(
        ModelProvider.GEMINI, base_url="https://generativelanguage.googleapis.com/v1beta"))),
    ("claude", lambda: ClaudeClient(_config(
        ModelProvider.CLAUDE, base_url="https://api.anthropic.com/v1"))),
    ("doubao", lambda: DoubaoClient(_config(
        ModelProvider.DOUBAO, base_url="https://ark.cn-beijing.volces.com/api/v3"))),
]


# 每个 provider 在"内容为空"时的报文形态
EMPTY_PAYLOADS = {
    "openai": {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
    "gemini": {"candidates": [{"content": {"parts": []}}]},
    "claude": {"content": []},
    "doubao": {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
}

# 每个 provider 在"正常返回"时的报文形态
OK_PAYLOADS = {
    "openai": {"choices": [{"message": {"content": "hello"}}],
               "usage": {"total_tokens": 11}},
    "gemini": {"candidates": [{"content": {"parts": [{"text": "hello"}]}}],
               "usageMetadata": {"totalTokenCount": 11}},
    "claude": {"content": [{"type": "text", "text": "hello"}],
               "usage": {"input_tokens": 5, "output_tokens": 6}},
    "doubao": {"choices": [{"message": {"content": "hello"}}],
               "usage": {"total_tokens": 11}},
}


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _patch_urlopen(monkeypatch, module, payload):
    monkeypatch.setattr(module, "urlopen", lambda *a, **k: _Response(payload))


def _patch_raise(monkeypatch, module, exc):
    def _boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(module, "urlopen", _boom)


def _module_for(name):
    """返回真正发起 HTTP 调用的模块。

    DoubaoClient 继承 OpenAIClient，其 urlopen 位于 openai_client 模块。
    """
    import importlib

    actual = "openai" if name == "doubao" else name
    return importlib.import_module(
        f"office_agent.model_gateway.clients.{actual}_client"
    )


# ------------------------------------------------------------ 成功


@pytest.mark.parametrize("name,factory", CLIENTS)
def test_success_response_shape_is_consistent(monkeypatch, name, factory):
    _patch_urlopen(monkeypatch, _module_for(name), OK_PAYLOADS[name])
    result = factory().chat([{"role": "user", "content": "hi"}])
    assert result.success is True
    assert result.content == "hello"
    assert result.tokens_used == 11      # 三家都把 usage 归一到 tokens_used
    assert result.error == ""
    assert result.model_used.endswith("-default")


# ------------------------------------------------------------ 空响应


@pytest.mark.parametrize("name,factory", CLIENTS)
def test_empty_content_is_a_failure_not_silent_success(monkeypatch, name, factory):
    """核心用例：空正文一律失败，不允许 success=True + 空串。"""
    _patch_urlopen(monkeypatch, _module_for(name), EMPTY_PAYLOADS[name])
    result = factory().chat([{"role": "user", "content": "hi"}])
    assert result.success is False
    assert "空内容" in result.error


@pytest.mark.parametrize("name,factory", CLIENTS)
def test_missing_content_key_is_a_failure(monkeypatch, name, factory):
    """报文里根本没有内容字段 -> 失败，而不是空串成功。"""
    _patch_urlopen(monkeypatch, _module_for(name), {})
    result = factory().chat([{"role": "user", "content": "hi"}])
    assert result.success is False
    assert result.error


# ------------------------------------------------------------ HTTP / 连接 / 超时


@pytest.mark.parametrize("name,factory", CLIENTS)
def test_http_error_is_reported_with_status_code(monkeypatch, name, factory):
    exc = HTTPError("https://x", 429, "Too Many Requests", {}, None)
    _patch_raise(monkeypatch, _module_for(name), exc)
    result = factory().chat([{"role": "user", "content": "hi"}])
    assert result.success is False
    assert "429" in result.error


@pytest.mark.parametrize("name,factory", CLIENTS)
def test_timeout_is_distinguishable_from_generic_failure(monkeypatch, name, factory):
    """超时必须有独立语义，不能混进通用"请求失败"。"""
    _patch_raise(monkeypatch, _module_for(name), TimeoutError("timed out"))
    result = factory().chat([{"role": "user", "content": "hi"}])
    assert result.success is False
    assert "超时" in result.error


@pytest.mark.parametrize("name,factory", CLIENTS)
def test_connection_error_is_reported_consistently(monkeypatch, name, factory):
    _patch_raise(monkeypatch, _module_for(name), URLError("dns failure"))
    result = factory().chat([{"role": "user", "content": "hi"}])
    assert result.success is False
    assert "连接错误" in result.error


# ------------------------------------------------------------ 异常报文 / usage


@pytest.mark.parametrize("name,factory", CLIENTS)
def test_malformed_response_does_not_raise(monkeypatch, name, factory):
    """畸形报文必须收敛成失败响应，不能向上抛异常。"""
    _patch_urlopen(monkeypatch, _module_for(name), {"unexpected": {"deep": 1}})
    result = factory().chat([{"role": "user", "content": "hi"}])
    assert result.success is False
    assert result.error


@pytest.mark.parametrize("name,factory", CLIENTS)
def test_missing_usage_does_not_crash(monkeypatch, name, factory):
    """usage 缺失不得让解析崩溃。"""
    payload = json.loads(json.dumps(OK_PAYLOADS[name]))
    payload.pop("usage", None)
    payload.pop("usageMetadata", None)
    _patch_urlopen(monkeypatch, _module_for(name), payload)
    result = factory().chat([{"role": "user", "content": "hi"}])
    assert result.success is True
    assert result.content == "hello"
    assert result.tokens_used == 0


@pytest.mark.parametrize("name,factory", CLIENTS)
def test_non_json_body_is_a_failure(monkeypatch, name, factory):
    class _Bad:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"<html>502 Bad Gateway</html>"

    monkeypatch.setattr(_module_for(name), "urlopen", lambda *a, **k: _Bad())
    result = factory().chat([{"role": "user", "content": "hi"}])
    assert result.success is False
    assert result.error


# ------------------------------------------------------------ 多模态输入结构一致


@pytest.mark.parametrize("name,factory", CLIENTS)
def test_multimodal_input_structure_is_provider_independent(monkeypatch, name, factory):
    """调用方不应因为换 provider 而改变输入结构。

    analyze_image 的路径与异常语义由 base 统一提供：
    supports_vision=False 时三家都返回同样的"不支持图片分析"。
    """
    client = factory()
    client.config.supports_vision = False
    result = client.analyze_image(__file__, "描述一下")
    assert result.success is False
    assert "不支持图片分析" in result.error
