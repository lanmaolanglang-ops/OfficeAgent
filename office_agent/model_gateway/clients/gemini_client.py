"""
Google Gemini 客户端
使用 Gemini API
"""
import json
import time
from typing import Optional
from urllib.request import Request, urlopen

from .base import BaseModelClient
from ...models.model_schemas import ModelConfig, ModelResponse
from ...vision_gateway.clients.gemini_client import GeminiVisionClient


class GeminiClient(BaseModelClient):
    """Google Gemini 客户端"""

    _vision_client_cls = GeminiVisionClient

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        # Gemini API URL 格式
        self.api_url = f"{self.base_url}/models/{self.model}:generateContent"
    
    def chat(self, messages: list[dict[str, str]],
             system_prompt: Optional[str] = None,
             temperature: Optional[float] = None,
             max_tokens: Optional[int] = None,
             **kwargs) -> ModelResponse:
        start = time.time()
        
        # 构建 Gemini 格式的 contents
        contents = []
        
        # 添加 system instruction
        system_instruction = None
        if system_prompt:
            system_instruction = {
                "parts": [{"text": system_prompt}]
            }
        
        for msg in messages:
            role = msg.get("role", "user")
            # Gemini 使用 "user" 和 "model"
            gemini_role = "user" if role == "user" else "model"
            contents.append({
                "role": gemini_role,
                "parts": [{"text": msg.get("content", "")}]
            })
        
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": self._get_temperature(temperature),
                "maxOutputTokens": self._get_max_tokens(max_tokens),
            }
        }
        
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        
        url = self.api_url
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }
        
        try:
            data = json.dumps(payload).encode("utf-8")
            req = Request(url, data=data, headers=headers, method="POST")
            
            with urlopen(req, timeout=self.config.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            
            # 解析响应
            content = ""
            candidates = result.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                for part in parts:
                    content += part.get("text", "")
            
            # 空响应不得报成功（与 vision_gateway 及 OpenAI 客户端一致）
            empty = self._empty_content_error(content, start)
            if empty:
                return empty

            # token 统计（usage 缺失不能让调用崩溃）
            usage = result.get("usageMetadata", {}) or {}
            tokens = usage.get("totalTokenCount", 0)

            return self._make_response(content, start, tokens, result)

        except Exception as e:
            # HTTP / 超时 / 连接 / 解析异常统一映射，切换 provider 语义一致
            return self._make_transport_error(e, start)
    
    # analyze_image 由 BaseModelClient 提供，委托 GeminiVisionClient 实现。
