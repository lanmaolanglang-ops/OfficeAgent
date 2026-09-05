"""Provider-neutral image generation gateway for document agents."""
import base64
import binascii
import ipaddress
import json
import os
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from .config import normalize_image_model_config


class ImageGenerationError(RuntimeError):
    pass


MAX_IMAGE_DOWNLOAD_BYTES = 20 * 1024 * 1024

# 默认生图尺寸（横向 4:3，适配 PPT 配图）；调用方可显式覆盖。
DEFAULT_IMAGE_SIZE = "1024x768"


def _validate_remote_url(url: str) -> None:
    """只允许解析到公网地址的 HTTP(S) 图片 URL。"""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise ImageGenerationError("图像下载仅支持 HTTP(S) URL")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ImageGenerationError("图像下载 URL 主机无效")
        host = parsed.hostname.rstrip(".").lower()
        if host == "localhost" or host.endswith(".localhost"):
            raise ImageGenerationError("拒绝下载内网图像地址")
        addresses = {
            item[4][0].split("%", 1)[0]
            for item in socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
        }
        if not addresses:
            raise ImageGenerationError("图像下载主机无法解析")
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ImageGenerationError("拒绝下载内网或保留地址上的图像")
    except ImageGenerationError:
        raise
    except (OSError, ValueError) as exc:
        raise ImageGenerationError(f"图像下载 URL 无效: {exc}") from exc


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_remote_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _decode_image_b64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, TypeError, ValueError) as exc:
        raise ImageGenerationError("图像服务返回了非法 base64 数据") from exc


def _response_error_message(body: str) -> str:
    """Extract a short provider message without echoing an entire response."""
    text = (body or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
        error = payload.get("error", payload) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail")
            if isinstance(message, str):
                return message.strip()[:300]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return text[:300]


def _post_json(url: str, payload: dict, headers: dict, timeout: float = 120.0) -> dict:
    """POST JSON and parse the response (zero-dependency, urllib-based)."""
    req = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except HTTPError as exc:
        try:
            detail = _response_error_message(exc.read().decode("utf-8", errors="replace"))
        except Exception:
            detail = ""
        suffix = f": {detail}" if detail else ""
        raise ImageGenerationError(f"图像服务请求失败: HTTP {exc.code}{suffix}") from exc
    except URLError as exc:
        raise ImageGenerationError(f"图像服务连接失败: {exc.reason}") from exc
    return json.loads(body)


def _get_bytes(url: str, timeout: float = 120.0,
               max_bytes: int = MAX_IMAGE_DOWNLOAD_BYTES) -> bytes:
    """GET binary content，限制协议、目标地址和响应大小。"""
    _validate_remote_url(url)
    req = Request(url, method="GET")
    try:
        opener = build_opener(_SafeRedirectHandler())
        with opener.open(req, timeout=timeout) as resp:
            content_length = resp.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise ImageGenerationError("图像下载超过大小限制")
                except ValueError:
                    pass
            chunks = []
            total = 0
            while True:
                chunk = resp.read(min(64 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ImageGenerationError("图像下载超过大小限制")
                chunks.append(chunk)
            return b"".join(chunks)
    except ImageGenerationError:
        raise
    except (HTTPError, URLError) as exc:
        raise ImageGenerationError(f"图像下载失败: {exc}") from exc


class ImageGenerationGateway:
    def __init__(self, api_key: str = "", base_url: str = "", model: str = "",
                 provider: str = "", mcp_url: str = ""):
        normalized = normalize_image_model_config({
            "provider": provider or os.getenv("IMAGE_PROVIDER", "agnes"),
            "api_key": api_key or os.getenv("AGNES_API_KEY") or os.getenv("CODEX_ENV_AGNES_API_KEY", ""),
            "base_url": base_url or os.getenv("AGNES_BASE_URL", ""),
            "model": model or os.getenv("AGNES_IMAGE_MODEL", ""),
            "mcp_url": mcp_url or os.getenv("IMAGE_MCP_URL", ""),
        })
        self.provider = normalized["provider"]
        self.mcp_url = normalized["mcp_url"]
        self.mcp_tool = os.getenv("IMAGE_MCP_TOOL", "generate_image")
        self.api_key = normalized["api_key"]
        self.base_url = normalized["base_url"]
        self.model = normalized["model"]

    def available(self) -> bool:
        return bool(self.mcp_url) if self.provider == "mcp" else bool(self.api_key)

    def generate(self, prompt: str, size: str = DEFAULT_IMAGE_SIZE, output_dir: Optional[str] = None) -> str:
        if not self.available():
            raise ImageGenerationError("未配置图像生成服务")
        if self.provider == "mcp":
            return self._generate_mcp(prompt, size, output_dir)
        data = _post_json(
            f"{self.base_url}/images/generations",
            {"model": self.model, "prompt": prompt, "size": size,
             "response_format": "url"},
            {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        items = data.get("data", [])
        if not items:
            raise ImageGenerationError("图像服务未返回图片")
        item = items[0]
        target_dir = Path(output_dir or tempfile.gettempdir())
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"agnes_ppt_image_{uuid.uuid4().hex[:8]}.png"
        if item.get("b64_json"):
            target.write_bytes(_decode_image_b64(item["b64_json"]))
        elif item.get("url"):
            target.write_bytes(_get_bytes(item["url"]))
        else:
            raise ImageGenerationError("图像服务返回内容为空")
        return str(target)

    def test_connection(self, output_dir: Optional[str] = None) -> dict:
        """Run one real generation, verify the downloaded file, then remove it."""
        started = time.monotonic()
        path = ""
        try:
            path = self.generate(
                "极简商务演示测试图，抽象蓝色光影，横向构图，无文字，无水印。",
                size=DEFAULT_IMAGE_SIZE,
                output_dir=output_dir,
            )
            target = Path(path)
            if not target.is_file() or target.stat().st_size <= 0:
                raise ImageGenerationError("图像服务未生成可用文件")
            return {
                "success": True,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "bytes": target.stat().st_size,
            }
        finally:
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass

    def _generate_mcp(self, prompt: str, size: str, output_dir: Optional[str]) -> str:
        """Call a configured MCP HTTP gateway using a tools/call envelope."""
        payload = _post_json(
            f"{self.mcp_url}/tools/call",
            {"name": self.mcp_tool, "arguments": {"prompt": prompt, "size": size}},
            {"Content-Type": "application/json"},
        )
        content = payload.get("result", payload).get("content", [])
        image_url = payload.get("url")
        image_b64 = payload.get("b64_json")
        for item in content if isinstance(content, list) else []:
            if isinstance(item, dict):
                image_url = image_url or item.get("url")
                image_b64 = image_b64 or item.get("b64_json")
        target = (Path(output_dir or tempfile.gettempdir()) /
                  f"mcp_ppt_image_{uuid.uuid4().hex[:8]}.png")
        if image_b64:
            target.write_bytes(_decode_image_b64(image_b64))
        elif image_url:
            target.write_bytes(_get_bytes(image_url))
        else:
            raise ImageGenerationError("MCP 图像工具未返回图片")
        return str(target)
