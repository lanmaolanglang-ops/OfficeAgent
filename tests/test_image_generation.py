import base64

from office_agent.image_generation import gateway as gw


def test_image_gateway_requires_key(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_ENV_AGNES_API_KEY", raising=False)
    assert not gw.ImageGenerationGateway().available()


def test_image_gateway_saves_base64(monkeypatch, tmp_path):
    monkeypatch.setattr(
        gw, "_post_json",
        lambda url, payload, headers, timeout=120.0: {"data": [{"b64_json": base64.b64encode(b"png").decode()}]},
    )
    gateway = gw.ImageGenerationGateway(api_key="test-key")
    path = gateway.generate("a cover", output_dir=str(tmp_path))
    assert open(path, "rb").read() == b"png"


def test_mcp_gateway_saves_base64(monkeypatch, tmp_path):
    monkeypatch.setattr(
        gw, "_post_json",
        lambda url, payload, headers, timeout=120.0: {"result": {"content": [{"b64_json": base64.b64encode(b"mcp").decode()}]}},
    )
    gateway = gw.ImageGenerationGateway(provider="mcp")
    gateway.mcp_url = "http://mcp"
    path = gateway.generate("a cover", output_dir=str(tmp_path))
    assert open(path, "rb").read() == b"mcp"
