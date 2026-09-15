"""
Anthropic Claude 客户端
使用 Anthropic Messages API
"""
import json
import time
from typing import Optional
from urllib.request import Request, urlopen

from .base import BaseModelClient
from ...models.model_schemas import ModelConfig, ModelResponse
from ...vision_gateway.clients.claude_client import ClaudeVisionClient
from ...security.endpoint_policy import join_api_endpoint, request_json


class ClaudeClient(BaseModelClient):
    """Anthropic Claude 客户端"""

    _vision_client_cls = ClaudeVisionClient

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        self.api_url = join_api_endpoint(self.base_url, "messages")
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
            custom_meta = self.config.extra_params or {}
            if getattr(self.config, "provider", None) and self.config.provider.value == "custom":
                result = request_json(
                    self.api_url,
                    method="POST",
                    headers=headers,
                    payload=payload,
                    timeout=self.config.timeout,
                    allow_local=bool(custom_meta.get("allow_local_endpoint", False)),
                )
            else:
                data = json.dumps(payload).encode("utf-8")
                req = Request(self.api_url, data=data, headers=headers, method="POST")
                with urlopen(req, timeout=self.config.timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
            
            # 解析响应
            content = ""
            for block in result.get("content", []):
                if block.get("type") == "text":
                    content += block.get("text", "")
            
            # 空响应不得报成功（与 vision_gateway 及 OpenAI 客户端一致）
            empty = self._empty_content_error(content, start)
            if empty:
                return empty

            usage = result.get("usage", {}) or {}
            tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

            return self._make_response(content, start, tokens, result)

        except Exception as e:
            # HTTP / 超时 / 连接 / 解析异常统一映射，切换 provider 语义一致
            return self._make_transport_error(e, start)
    
    # analyze_image 由 BaseModelClient 提供，委托 ClaudeVisionClient 实现。
