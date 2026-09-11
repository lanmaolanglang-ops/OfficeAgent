"""
模型客户端基类
定义统一的模型调用接口
"""
import socket
import time
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Any, Optional
from urllib.error import HTTPError, URLError

from ...models.model_schemas import ModelConfig, ModelResponse, ChatMessage


class BaseModelClient(ABC):
    """模型客户端基类"""

    # 各提供商视觉调用的唯一实现位于 vision_gateway.clients；
    # 子类在此声明对应的 BaseVisionClient 实现，analyze_image 仅作兼容外观。
    _vision_client_cls: type[Any] | None = None

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
            path = Path(file_path)
            ext = path.suffix.lower()
            if ext in {".docx", ".pptx", ".xlsx", ".csv", ".pdf", ".md", ".txt"}:
                from ...knowledge_base.document_parser import DocumentParser
                content = DocumentParser().parse(str(path)).full_text
            else:
                # 未知格式只在能严格解码为文本时接受，禁止把二进制文件
                # errors=ignore 后的乱码泄露给模型。
                content = path.read_text(encoding="utf-8", errors="strict")

            if not content.strip():
                return ModelResponse(
                    success=False,
                    error="文档未提取到可分析的文本内容",
                    model_used=self.config.id,
                )
            
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
        """图片分析（兼容外观）

        真实实现唯一来源是 vision_gateway.clients 中对应提供商的视觉
        客户端；本方法只负责参数转换、大小防御和 ModelResponse 适配，
        保留原有 import path、超时与配置语义。
        """
        start = time.time()

        if self._vision_client_cls is None or not self.config.supports_vision:
            return ModelResponse(
                success=False,
                error=f"{self.config.display_name} 不支持图片分析",
                model_used=self.config.id,
            )

        # 大小防御在读取文件前完成
        try:
            max_image_bytes = int(self.config.extra_params.get(
                "max_image_bytes", 20 * 1024 * 1024
            ))
        except (TypeError, ValueError):
            max_image_bytes = 20 * 1024 * 1024
        try:
            image_size = Path(image_path).stat().st_size
        except OSError as e:
            return self._make_error(f"图片读取失败: {e}", start)
        if image_size > max_image_bytes:
            return self._make_error(
                f"图片超过请求大小限制（{max_image_bytes} bytes）", start
            )

        from ...vision_gateway.vision_models import ImageInput, VisionRequest
        client = self._vision_client_cls(
            self.api_key, self.model, self.base_url, self.config.display_name
        )
        client.timeout = self.config.timeout
        vision_request = VisionRequest(
            images=[ImageInput.from_file(image_path)],
            prompt=prompt,
            system_prompt=system_prompt or "",
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            require_structured=False,
        )
        resp = client.analyze(vision_request)
        return ModelResponse(
            success=resp.success,
            content=resp.content,
            model_used=self.config.id,
            provider=resp.provider,
            tokens_used=resp.tokens_used,
            latency_ms=resp.latency_ms,
            error=resp.error,
            raw_response=resp.raw_response,
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

    # === 三家供应商客户端共享的语义收敛（P1-9） ===
    # 厂商 API 的报文格式可以不同，但上层切换 provider 时得到的
    # 成功/失败/超时/空响应语义必须一致。

    def _make_transport_error(self, exc: BaseException,
                              start_time: float) -> ModelResponse:
        """把底层传输异常映射为统一的错误语义。

        过去三家各自 catch：HTTP/连接错误文案虽一致，但超时被混进通用
        "请求失败"，调用方无法区分可重试的超时。
        """
        if isinstance(exc, HTTPError):
            try:
                body = exc.read().decode("utf-8", errors="ignore")[:200]
            except Exception:
                body = ""
            return self._make_error(
                f"HTTP {exc.code}: {exc.reason} {body}".strip(), start_time
            )
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return self._make_error(
                f"请求超时（{self.config.timeout}s）: {exc}", start_time
            )
        if isinstance(exc, URLError):
            return self._make_error(f"连接错误: {exc.reason}", start_time)
        return self._make_error(f"请求失败: {exc}", start_time)

    def _empty_content_error(self, content: str,
                             start_time: float) -> Optional[ModelResponse]:
        """空响应一律视为失败，而不是 success=True + 空串。

        与 vision_gateway 既有约定一致（"模型返回空内容"）：
        成功却拿到空正文会让上层把空结果当成有效产物。
        """
        if not content:
            return self._make_error("模型返回空内容", start_time)
        return None
    
    def _get_temperature(self, temperature: Optional[float]) -> float:
        return temperature if temperature is not None else self.config.temperature
    
    def _get_max_tokens(self, max_tokens: Optional[int]) -> int:
        return max_tokens if max_tokens is not None else self.config.max_tokens
