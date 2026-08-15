"""
OpenAI 兼容客户端
支持：OpenAI、DeepSeek、通义千问、以及其他兼容 OpenAI API 格式的服务
"""
import json
import time
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from .base import BaseModelClient
from ...models.model_schemas import ModelConfig, ModelResponse


class OpenAIClient(BaseModelClient):
    """OpenAI 兼容 API 客户端"""
    
    def __init__(self, config: ModelConfig):
        super().__init__(config)
        # 确保 base_url 正确
        if not self.base_url.endswith("/chat/completions"):
            self.chat_url = f"{self.base_url}/chat/completions"
        else:
            self.chat_url = self.base_url
    
    def chat(self, messages: list[dict[str, str]],
             system_prompt: Optional[str] = None,
             temperature: Optional[float] = None,
             max_tokens: Optional[int] = None,
             **kwargs) -> ModelResponse:
        start = time.time()
        
        # 构建消息列表
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        
        # 构建请求体
        payload = {
            "model": self.model,
            "messages": full_messages,
            "temperature": self._get_temperature(temperature),
            "max_tokens": self._get_max_tokens(max_tokens),
            "stream": False,
        }
        
        # 支持视觉的模型添加图片支持参数
        if self.config.supports_vision:
            # 这里不自动添加，由调用方在 messages 中构造多模态内容
            pass
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        
        try:
            data = json.dumps(payload).encode("utf-8")
            req = Request(self.chat_url, data=data, headers=headers, method="POST")
            
            with urlopen(req, timeout=self.config.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            
            content = result["choices"][0]["message"]["content"]
            usage = result.get("usage", {})
            tokens = usage.get("total_tokens", 0)
            
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
        """图片分析（使用 base64 编码，支持 GPT-4V 等）"""
        import base64
        from pathlib import Path
        
        start = time.time()
        
        if not self.config.supports_vision:
            return self._make_error("当前模型不支持视觉分析", start)
        
        try:
            # 读取图片并 base64 编码
            img_data = Path(image_path).read_bytes()
            img_b64 = base64.b64encode(img_data).decode("utf-8")
            
            # 判断 MIME 类型
            ext = Path(image_path).suffix.lower()
            mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
            mime = mime_map.get(ext, "image/png")
            
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{img_b64}"}
                        }
                    ]
                }
            ]
            
            if system_prompt:
                messages.insert(0, {"role": "system", "content": system_prompt})
            
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": self._get_temperature(None),
                "max_tokens": self._get_max_tokens(None),
            }
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            
            data = json.dumps(payload).encode("utf-8")
            req = Request(self.chat_url, data=data, headers=headers, method="POST")
            
            with urlopen(req, timeout=self.config.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            
            content = result["choices"][0]["message"]["content"]
            tokens = result.get("usage", {}).get("total_tokens", 0)
            
            return self._make_response(content, start, tokens, result)
            
        except Exception as e:
            return self._make_error(f"图片分析失败: {str(e)}", start)
