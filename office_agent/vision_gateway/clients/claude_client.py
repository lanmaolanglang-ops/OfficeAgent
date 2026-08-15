"""
Anthropic Claude Vision 客户端
支持 Claude 3 Opus, Sonnet, Haiku
"""
import json
import time
from typing import Optional

from .base import BaseVisionClient
from ..vision_models import VisionRequest, VisionResponse


class ClaudeVisionClient(BaseVisionClient):
    """Anthropic Claude Vision 客户端"""

    def __init__(self, api_key: str, model: str = "claude-3-sonnet-20240229",
                 base_url: str = "https://api.anthropic.com/v1",
                 display_name: str = ""):
        super().__init__(api_key, model, base_url, display_name or "Claude Vision")

    @property
    def provider_name(self) -> str:
        return "claude"

    def analyze(self, request: VisionRequest) -> VisionResponse:
        start = time.time()

        if not request.images:
            return self._make_error("没有提供图片", start)

        try:
            # Claude 格式
            content = []

            # 图片
            for img in request.images:
                b64 = img.to_base64()
                if b64:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img.get_mime(),
                            "data": b64,
                        }
                    })
                elif img.url:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": img.url,
                        }
                    })

            # 文本
            system_prompt = self._build_system_prompt(request)
            user_prompt = self._build_user_prompt(request)
            content.append({"type": "text", "text": user_prompt})

            payload = {
                "model": self.model,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "messages": [{"role": "user", "content": content}],
            }

            if system_prompt:
                payload["system"] = system_prompt

            headers = {
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            }

            data = json.dumps(payload).encode("utf-8")
            resp = self._http_post(
                f"{self.base_url}/messages",
                headers, data
            )

            # 解析
            content_list = resp.get("content", [])
            text = "\n".join(b.get("text", "") for b in content_list if b.get("type") == "text")

            usage = resp.get("usage", {})
            tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

            result = self._make_response(text, start, tokens, resp)

            if request.require_structured:
                result.structured = self._parse_structured(text, request)

            return result

        except Exception as e:
            return self._make_error(f"Claude Vision 调用失败: {e}", start)
