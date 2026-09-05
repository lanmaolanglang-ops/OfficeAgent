"""
OpenAI GPT Vision 客户端
支持 GPT-4o, GPT-4 Turbo with Vision, GPT-4V
"""
import json
import time

from .base import BaseVisionClient
from ..vision_models import VisionRequest, VisionResponse


class OpenAIVisionClient(BaseVisionClient):
    """OpenAI GPT Vision 客户端"""

    def __init__(self, api_key: str, model: str = "gpt-4o",
                 base_url: str = "https://api.openai.com/v1",
                 display_name: str = ""):
        super().__init__(api_key, model, base_url, display_name or "GPT-4o")

    @property
    def provider_name(self) -> str:
        return "openai"

    def analyze(self, request: VisionRequest) -> VisionResponse:
        start = time.time()

        if not request.images:
            return self._make_error("没有提供图片", start)

        try:
            # 构建消息内容
            content: list[dict] = []

            # 文本提示
            user_prompt = self._build_user_prompt(request)
            content.append({"type": "text", "text": user_prompt})

            # 图片
            for img in request.images:
                if img.source.value == "url" and img.url:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": img.url}
                    })
                else:
                    b64 = img.to_base64()
                    if b64:
                        mime = img.get_mime()
                        content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"}
                        })

            messages = [{"role": "user", "content": content}]

            # 系统提示
            system_prompt = self._build_system_prompt(request)
            if system_prompt:
                messages.insert(0, {"role": "system", "content": system_prompt})

            payload = {
                "model": self.model,
                "messages": messages,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
            }

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }

            data = json.dumps(payload).encode("utf-8")
            resp = self._http_post(
                f"{self.base_url}/chat/completions",
                headers, data
            )

            text = resp["choices"][0]["message"]["content"]
            tokens = resp.get("usage", {}).get("total_tokens", 0)

            result = self._make_response(text, start, tokens, resp)

            # 解析结构化结果
            if request.require_structured:
                result.structured = self._parse_structured(text, request)

            return result

        except Exception as e:
            return self._make_error(f"OpenAI Vision 调用失败: {e}", start)
