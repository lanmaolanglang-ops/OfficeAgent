"""
模型客户端基类
定义统一的模型调用接口
"""
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from ...models.model_schemas import ModelConfig, ModelResponse, ChatMessage


class BaseModelClient(ABC):
    """模型客户端基类"""
    
    def __init__(self, config: ModelConfig):
        self.config = config
        self.api_key = config.api_key
        self.base_url = config.base_url.rstrip("/")
        self.model = config.model
    
    @abstractmethod
    def chat(self, messages: list[dict[str, str]],
             system_prompt: Optional[str] = None,
             temperature: Optional[float] = None,
             max_tokens: Optional[int] = None,
             **kwargs) -> ModelResponse:
        """
        同步聊天调用
        
        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            system_prompt: 系统提示词
            temperature: 温度
            max_tokens: 最大token数
        """
        pass
    
    def chat_with_messages(self, messages: list[ChatMessage],
                          system_prompt: Optional[str] = None,
                          **kwargs) -> ModelResponse:
        """使用 ChatMessage 对象列表调用"""
        msg_dicts = [m.to_dict() for m in messages]
        return self.chat(msg_dicts, system_prompt=system_prompt, **kwargs)
    
    def simple_chat(self, user_message: str,
                   system_prompt: Optional[str] = None,
                   **kwargs) -> ModelResponse:
        """简单单轮对话"""
        messages = [{"role": "user", "content": user_message}]
        return self.chat(messages, system_prompt=system_prompt, **kwargs)
    
    def analyze_document(self, file_path: str, prompt: str,
                        system_prompt: Optional[str] = None) -> ModelResponse:
        """
        文档分析（默认实现：读取文件内容后作为文本发送）
        支持视觉的子类可重写为真正的文件上传
        """
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            
            # 截断过长内容
            max_chars = self.config.max_tokens * 3  # 粗略估算
            if len(content) > max_chars:
                content = content[:max_chars] + "\n...(内容已截断)"
            
            full_prompt = f"{prompt}\n\n文档内容：\n{content}"
            return self.simple_chat(full_prompt, system_prompt=system_prompt)
        except Exception as e:
            return ModelResponse(
                success=False,
                error=f"文档读取失败: {str(e)}",
                model_used=self.config.id,
            )
    
    def analyze_image(self, image_path: str, prompt: str,
                     system_prompt: Optional[str] = None) -> ModelResponse:
        """
        图片分析（多模态）
        子类需要重写以支持真正的视觉能力
        """
        return ModelResponse(
            success=False,
            error=f"{self.config.display_name} 不支持图片分析",
            model_used=self.config.id,
        )
    
    def test_connection(self) -> ModelResponse:
        """测试连接"""
        return self.simple_chat("Hello, respond with 'OK' if you receive this.")
    
    def _make_response(self, content: str,
                      start_time: float,
                      tokens: int = 0,
                      raw: Any = None) -> ModelResponse:
        """构造成功响应"""
        return ModelResponse(
            success=True,
            content=content,
            model_used=self.config.id,
            provider=self.config.provider.value,
            tokens_used=tokens,
            latency_ms=int((time.time() - start_time) * 1000),
            raw_response=raw,
        )
    
    def _make_error(self, error: str,
                   start_time: float) -> ModelResponse:
        """构造错误响应"""
        return ModelResponse(
            success=False,
            error=error,
            model_used=self.config.id,
            provider=self.config.provider.value,
            latency_ms=int((time.time() - start_time) * 1000),
        )
    
    def _get_temperature(self, temperature: Optional[float]) -> float:
        return temperature if temperature is not None else self.config.temperature
    
    def _get_max_tokens(self, max_tokens: Optional[int]) -> int:
        return max_tokens if max_tokens is not None else self.config.max_tokens
