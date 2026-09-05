"""
Google Gemini Vision 客户端
支持 Gemini 1.5 Pro, Gemini Pro Vision
"""
import json
import time

from .base import BaseVisionClient
from ..vision_models import VisionRequest, VisionResponse


class GeminiVisionClient(BaseVisionClient):
    """Google Gemini Vision 客户端"""

    def __init__(self, api_key: str, model: str = "gemini-1.5-pro",
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta",
                 display_name: str = ""):
        super().__init__(api_key, model, base_url, display_name or "Gemini Vision")

    @property
    def provider_name(self) -> str:
        return "gemini"

    def analyze(self, request: VisionRequest) -> VisionResponse:
        start = time.time()

        if not request.images:
            return self._make_error("没有提供图片", start)

        try:
            # 构建 Gemini 格式
            parts: list[dict] = []

            # 系统提示 + 用户提示
            system_prompt = self._build_system_prompt(request)
            user_prompt = self._build_user_prompt(request)
            full_prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
            parts.append({"text": full_prompt})

            # 图片
            for img in request.images:
                b64 = img.to_base64()
                if b64:
                    parts.append({
                        "inlineData": {
                            "mimeType": img.get_mime(),
                            "data": b64,
                        }
                    })
                elif img.url:
                    parts.append({
                        "fileData": {
                            "mimeType": img.get_mime(),
                            "fileUri": img.url,
                        }
                    })

            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "maxOutputTokens": request.max_tokens,
                    "temperature": request.temperature,
                },
            }

            url = f"{self.base_url}/models/{self.model}:generateContent"
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            }
            data = json.dumps(payload).encode("utf-8")

            resp = self._http_post(url, headers, data)

            # 解析响应
            candidates = resp.get("candidates", [])
            if not candidates:
                return self._make_error("Gemini 未返回结果", start)

            content = candidates[0].get("content", {})
            parts_list = content.get("parts", [])
            text = "\n".join(p.get("text", "") for p in parts_list if "text" in p)

            # token 统计
            usage = resp.get("usageMetadata", {})
            tokens = usage.get("totalTokenCount", 0)

            result = self._make_response(text, start, tokens, resp)

            if request.require_structured:
                result.structured = self._parse_structured(text, request)

            return result

        except Exception as e:
            return self._make_error(f"Gemini Vision 调用失败: {e}", start)
