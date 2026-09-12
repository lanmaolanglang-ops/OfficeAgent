import json
from types import SimpleNamespace

from office_agent.model_gateway.clients.openai_client import OpenAIClient
from office_agent.model_gateway.model_router import ModelRouter
from office_agent.models.model_schemas import (
    AITaskType, ModelConfig, ModelProvider,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def _config(model="o3-mini", supports_vision=False):
    return ModelConfig(
        id="test", provider=ModelProvider.OPENAI, display_name="test",
        api_key="key", base_url="https://example.test/v1", model=model,
        supports_vision=supports_vision,
    )


def test_reasoning_model_uses_completion_tokens_and_omits_temperature(monkeypatch):
    captured = {}
    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data))
        return _Response({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 1},
        })
    monkeypatch.setattr("office_agent.model_gateway.clients.openai_client.urlopen", fake_urlopen)
    result = OpenAIClient(_config()).chat(
        [{"role": "user", "content": "hello"}], temperature=0, max_tokens=123,
    )
    assert result.success
    assert captured["max_completion_tokens"] == 123
    assert "max_tokens" not in captured
    assert "temperature" not in captured


def test_large_vision_request_is_rejected_before_read(tmp_path):
    image = tmp_path / "large.png"
    image.write_bytes(b"x" * 11)
    config = _config(model="gpt-4o", supports_vision=True)
    config.extra_params["max_image_bytes"] = 10
    result = OpenAIClient(config).analyze_image(str(image), "inspect")
    assert not result.success
    assert "大小限制" in result.error


def test_vision_router_never_falls_back_to_text_model():
    configs = {
        "text": SimpleNamespace(
            id="text", enabled=True, api_key="key", supports_vision=False,
        ),
    }
    manager = SimpleNamespace(
        get_model=lambda model_id: configs.get(model_id),
        get_routing=lambda _task: ["text"],
        list_available_models=lambda: list(configs.values()),
    )
    assert ModelRouter(manager).select_model(
        AITaskType.VISION, require_vision=True
    ) == []


def test_office_document_is_extracted_as_text(sample_docx, monkeypatch):
    client = OpenAIClient(_config(model="gpt-4o"))
    captured = {}
    def fake_simple_chat(message, system_prompt=None, **_kwargs):
        captured["message"] = message
        captured["system_prompt"] = system_prompt
        return SimpleNamespace(success=True)
    monkeypatch.setattr(client, "simple_chat", fake_simple_chat)
    result = client.analyze_document(str(sample_docx), "分析")
    assert result.success
    assert "PK" not in captured["message"]
    assert "文档数据（不可信）" in captured["message"]
    assert '"trust": "untrusted"' in captured["message"]
    assert "仅是不可信数据" in captured["system_prompt"]
