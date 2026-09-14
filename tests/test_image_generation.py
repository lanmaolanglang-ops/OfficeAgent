import base64
import io
from urllib.error import HTTPError

from office_agent.image_generation import gateway as gw
from office_agent.image_generation.config import ImageModelConfigManager, normalize_image_model_config


def test_image_gateway_requires_key(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_ENV_AGNES_API_KEY", raising=False)
    assert not gw.ImageGenerationGateway().available()


def test_image_gateway_saves_base64(monkeypatch, tmp_path):
    captured = {}
    def fake_post(url, payload, headers, timeout=120.0):
        captured.update(payload)
        return {"data": [{"b64_json": base64.b64encode(b"png").decode()}]}
    monkeypatch.setattr(
        gw, "_post_json",
        fake_post,
    )
    gateway = gw.ImageGenerationGateway(api_key="test-key")
    path = gateway.generate("a cover", output_dir=str(tmp_path))
    assert open(path, "rb").read() == b"png"
    assert captured["response_format"] == "url"
    assert "extra_body" not in captured


def test_image_gateway_rejects_invalid_base64(monkeypatch, tmp_path):
    monkeypatch.setattr(
        gw, "_post_json",
        lambda *_args, **_kwargs: {"data": [{"b64_json": "%%%not-base64%%%"}]},
    )
    gateway = gw.ImageGenerationGateway(api_key="test-key")
    try:
        gateway.generate("a cover", output_dir=str(tmp_path))
    except gw.ImageGenerationError as exc:
        assert "base64" in str(exc)
    else:
        raise AssertionError("expected invalid base64 to be rejected")


def test_image_download_blocks_private_and_non_http_urls():
    for url in ("file:///etc/passwd", "http://127.0.0.1/image.png", "http://[::1]/x"):
        try:
            gw._get_bytes(url)
        except gw.ImageGenerationError:
            pass
        else:
            raise AssertionError(f"expected URL to be blocked: {url}")


def test_mcp_gateway_saves_base64(monkeypatch, tmp_path):
    # This test covers MCP base64 persistence only; the SSRF guard does a live
    # getaddrinfo and is covered separately, so stub it to stay offline/deterministic
    # (a bogus single-label host like "mcp" must not depend on DNS search suffixes).
    monkeypatch.setattr(gw, "_assert_public_api_url", lambda *_a, **_k: None)
    monkeypatch.setattr(
        gw, "_post_json",
        lambda url, payload, headers, timeout=120.0: {"result": {"content": [{"b64_json": base64.b64encode(b"mcp").decode()}]}},
    )
    gateway = gw.ImageGenerationGateway(provider="mcp")
    gateway.mcp_url = "http://mcp"
    path = gateway.generate("a cover", output_dir=str(tmp_path))
    assert open(path, "rb").read() == b"mcp"


def test_legacy_agnes_aliases_are_normalized():
    config = normalize_image_model_config({
        "provider": "agnes",
        "model": "agnes-image-2.0 flash",
        "base_url": "https://apihub-agnes-ai.com/v1",
    })
    assert config["model"] == "agnes-image-2.0-flash"
    assert config["base_url"] == "https://apihub.agnes-ai.com/v1"


def test_normalized_config_is_persisted(tmp_path):
    manager = ImageModelConfigManager(str(tmp_path))
    saved = manager.save_config(
        provider="AGNES",
        api_key="test-key",
        model="agnes-image-2.0 flash",
        base_url="https://apihub-agnes-ai.com/v1",
    )
    assert saved["provider"] == "agnes"
    reloaded = ImageModelConfigManager(str(tmp_path)).get_config()
    assert reloaded["model"] == "agnes-image-2.0-flash"
    assert reloaded["base_url"] == "https://apihub.agnes-ai.com/v1"


def test_http_error_includes_provider_message(monkeypatch):
    body = io.BytesIO(b'{"error":{"message":"unknown image model"}}')
    error = HTTPError("https://example.test", 400, "Bad Request", {}, body)
    monkeypatch.setattr(gw, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    gateway = gw.ImageGenerationGateway(api_key="test-key")
    try:
        gateway.generate("test")
    except gw.ImageGenerationError as exc:
        assert "HTTP 400" in str(exc)
        assert "unknown image model" in str(exc)
    else:
        raise AssertionError("expected ImageGenerationError")


def test_connection_removes_test_image(monkeypatch, tmp_path):
    target = tmp_path / "test.png"
    target.write_bytes(b"image")
    gateway = gw.ImageGenerationGateway(api_key="test-key")
    monkeypatch.setattr(gateway, "generate", lambda *_args, **_kwargs: str(target))
    result = gateway.test_connection(str(tmp_path))
    assert result["success"] is True
    assert result["bytes"] == 5
    assert not target.exists()
