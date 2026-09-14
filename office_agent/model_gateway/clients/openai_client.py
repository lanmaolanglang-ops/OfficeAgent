"""
OpenAI 兼容客户端
支持：OpenAI、DeepSeek、通义千问、以及其他兼容 OpenAI API 格式的服务
"""
import json
import re
import time
from typing import Optional
from urllib.request import Request, urlopen

from .base import BaseModelClient
from ...models.model_schemas import ModelConfig, ModelResponse
from ...vision_gateway.clients.openai_client import OpenAIVisionClient


class OpenAIClient(BaseModelClient):
    """OpenAI 兼容 API 客户端"""

    _vision_client_cls = OpenAIVisionClient

    def __init__(self, config: ModelConfig):
        super().__init__(config)
        # 确保 base_url 正确
        if not self.base_url.endswith("/chat/completions"):
            self.chat_url = f"{self.base_url}/chat/completions"
        else:
            self.chat_url = self.base_url

    def _is_reasoning_model(self) -> bool:
        name = (self.model or "").lower()
        return bool(re.match(r"^(o1|o3|o4)(?:-|$)", name) or name.startswith("gpt-5"))

    def _apply_generation_params(self, payload: dict,
                                 temperature: Optional[float],
                                 max_tokens: Optional[int]) -> None:
        token_limit = self._get_max_tokens(max_tokens)
        if self._is_reasoning_model():
            payload["max_completion_tokens"] = token_limit
        else:
            payload["temperature"] = self._get_temperature(temperature)
            payload["max_tokens"] = token_limit
    
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
            "stream": False,
        }
        self._apply_generation_params(payload, temperature, max_tokens)
        # DeepSeek OpenAI 格式使用 thinking.type 控制思考模式；旧的
        # reasoning.enabled 不是官方 Chat Completions 参数，兼容网关可能直接 400。
        if getattr(self.config, "provider", None) and self.config.provider.value == "deepseek":
            payload["thinking"] = {"type": "disabled"}
        
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
            
            # P3-85: provider 可能返回缺失/空 choices，不能用 [0] 触发
            # IndexError 再被笼统映射成“传输错误”，应给出明确的空响应错误。
            choices = result.get("choices") or []
            if not choices:
                return self._make_error("模型返回空 choices（无候选响应）", start)
            choice = choices[0]
            message = choice.get("message", {}) or {}
            content = message.get("content") or ""
            finish_reason = choice.get("finish_reason", "")
            usage = result.get("usage", {})
            tokens = usage.get("total_tokens", 0)

            # 推理模型（deepseek-reasoner / deepseek-v4 等）会先输出 reasoning_content 思维链，
            # 当 max_tokens 不足以同时容纳思维链与正文时，content 会被截断为空。
            if not content:
                reasoning = message.get("reasoning_content", "") or ""
                if finish_reason == "length":
                    return self._make_error(
                        f"响应被 max_tokens 截断，正文未生成（思维链已占用 {len(reasoning)} 字），请增大 max_tokens",
                        start,
                    )
                if reasoning:
                    content = reasoning
                else:
                    return self._make_error("模型返回空内容", start)

            return self._make_response(content, start, tokens, result)
            
        except Exception as e:
            # HTTP / 超时 / 连接 / 解析异常统一映射，切换 provider 语义一致
            return self._make_transport_error(e, start)
    
    # analyze_image 由 BaseModelClient 提供，委托 OpenAIVisionClient 实现。
