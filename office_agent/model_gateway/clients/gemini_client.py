"""
Google Gemini 客户端
使用 Gemini API
"""
import json
import time
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

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
            
            # token 统计
            usage = result.get("usageMetadata", {})
            tokens = usage.get("totalTokenCount", 0)
            
            return self._make_response(content, start, tokens, result)
            
        except HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                pass
            return self._make_error(
                f"HTTP {e.code}: {e.reason} {error_body[:200]}", start
            )
        except URLError as e:
            return self._make_error(f"连接错误: {str(e.reason)}", start)
        except Exception as e:
            return self._make_error(f"请求失败: {str(e)}", start)
    
    # analyze_image 由 BaseModelClient 提供，委托 GeminiVisionClient 实现。
