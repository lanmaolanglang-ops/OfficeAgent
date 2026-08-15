"""
Anthropic Claude 客户端
使用 Anthropic Messages API
"""
import json
import time
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from .base import BaseModelClient
from ...models.model_schemas import ModelConfig, ModelResponse


class ClaudeClient(BaseModelClient):
    """Anthropic Claude 客户端"""
    
    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.api_url = f"{self.base_url}/messages"
        self.api_version = "2023-06-01"
    
    def chat(self, messages: list[dict[str, str]],
             system_prompt: Optional[str] = None,
             temperature: Optional[float] = None,
             max_tokens: Optional[int] = None,
             **kwargs) -> ModelResponse:
        start = time.time()
        
        # Claude API 格式：system 单独传，messages 中不能有 system role
        claude_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                if not system_prompt:
                    system_prompt = msg.get("content", "")
                continue
            claude_messages.append({
                "role": role,
                "content": msg.get("content", "")
            })
        
        payload = {
            "model": self.model,
            "messages": claude_messages,
            "max_tokens": self._get_max_tokens(max_tokens),
            "temperature": self._get_temperature(temperature),
        }
        
        if system_prompt:
            payload["system"] = system_prompt
        
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
        }
        
        try:
            data = json.dumps(payload).encode("utf-8")
            req = Request(self.api_url, data=data, headers=headers, method="POST")
            
            with urlopen(req, timeout=self.config.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            
            # 解析响应
            content = ""
            for block in result.get("content", []):
                if block.get("type") == "text":
                    content += block.get("text", "")
            
            usage = result.get("usage", {})
            tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            
            return self._make_response(content, start, tokens, result)
            
        except HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except:
                pass
            return self._make_error(
                f"HTTP {e.code}: {e.reason} {error_body[:200]}", start
            )
        except URLError as e:
            return self._make_error(f"连接错误: {str(e.reason)}", start)
        except Exception as e:
            return self._make_error(f"请求失败: {str(e)}", start)
    
    def analyze_image(self, image_path: str, prompt: str,
                     system_prompt: Optional[str] = None) -> ModelResponse:
        """Claude 图片分析"""
        import base64
        from pathlib import Path
        
        start = time.time()
        
        try:
            img_data = Path(image_path).read_bytes()
            img_b64 = base64.b64encode(img_data).decode("utf-8")
            
            ext = Path(image_path).suffix.lower()
            mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
            mime = mime_map.get(ext, "image/png")
            
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": img_b64,
                            }
                        },
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
            
            payload = {
                "model": self.model,
                "messages": messages,
                "max_tokens": self._get_max_tokens(None),
                "temperature": self._get_temperature(None),
            }
            
            if system_prompt:
                payload["system"] = system_prompt
            
            headers = {
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": self.api_version,
            }
            
            data = json.dumps(payload).encode("utf-8")
            req = Request(self.api_url, data=data, headers=headers, method="POST")
            
            with urlopen(req, timeout=self.config.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            
            content = ""
            for block in result.get("content", []):
                if block.get("type") == "text":
                    content += block.get("text", "")
            
            usage = result.get("usage", {})
            tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            
            return self._make_response(content, start, tokens, result)
            
        except Exception as e:
            return self._make_error(f"图片分析失败: {str(e)}", start)
