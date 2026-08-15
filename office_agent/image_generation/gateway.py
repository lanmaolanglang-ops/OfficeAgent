"""Provider-neutral image generation gateway for document agents."""
import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class ImageGenerationError(RuntimeError):
    pass


def _post_json(url: str, payload: dict, headers: dict, timeout: float = 120.0) -> dict:
    """POST JSON and parse the response (zero-dependency, urllib-based)."""
    req = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except HTTPError as exc:
        raise ImageGenerationError(f"图像服务请求失败: HTTP {exc.code}") from exc
    except URLError as exc:
        raise ImageGenerationError(f"图像服务连接失败: {exc.reason}") from exc
    return json.loads(body)


def _get_bytes(url: str, timeout: float = 120.0) -> bytes:
    """GET binary content (zero-dependency, urllib-based)."""
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (HTTPError, URLError) as exc:
        raise ImageGenerationError(f"图像下载失败: {exc}") from exc


class ImageGenerationGateway:
    def __init__(self, api_key: str = "", base_url: str = "", model: str = "",
                 provider: str = "", mcp_url: str = ""):
        self.provider = provider or os.getenv("IMAGE_PROVIDER", "agnes")
        self.mcp_url = (mcp_url or os.getenv("IMAGE_MCP_URL", "")).rstrip("/")
        self.mcp_tool = os.getenv("IMAGE_MCP_TOOL", "generate_image")
        self.api_key = api_key or os.getenv("AGNES_API_KEY") or os.getenv("CODEX_ENV_AGNES_API_KEY", "")
        self.base_url = (base_url or os.getenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")).rstrip("/")
        self.model = model or os.getenv("AGNES_IMAGE_MODEL", "agnes-image-2.0-flash")

    def available(self) -> bool:
        return bool(self.mcp_url) if self.provider == "mcp" else bool(self.api_key)

    def generate(self, prompt: str, size: str = "1024x768", output_dir: Optional[str] = None) -> str:
        if not self.available():
            raise ImageGenerationError("未配置图像生成服务")
        if self.provider == "mcp":
            return self._generate_mcp(prompt, size, output_dir)
        data = _post_json(
            f"{self.base_url}/images/generations",
            {"model": self.model, "prompt": prompt, "size": size,
             "extra_body": {"response_format": "url"}},
            {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        items = data.get("data", [])
        if not items:
            raise ImageGenerationError("图像服务未返回图片")
        item = items[0]
        target_dir = Path(output_dir or tempfile.gettempdir())
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "agnes_ppt_image.png"
        if item.get("b64_json"):
            target.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            target.write_bytes(_get_bytes(item["url"]))
        else:
            raise ImageGenerationError("图像服务返回内容为空")
        return str(target)

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
        target = Path(output_dir or tempfile.gettempdir()) / "mcp_ppt_image.png"
        if image_b64:
            target.write_bytes(base64.b64decode(image_b64))
        elif image_url:
            target.write_bytes(_get_bytes(image_url))
        else:
            raise ImageGenerationError("MCP 图像工具未返回图片")
        return str(target)
