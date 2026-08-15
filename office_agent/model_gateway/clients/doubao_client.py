"""
豆包（火山引擎方舟）客户端
豆包 API 兼容 OpenAI 格式，使用 OpenAIClient 作为基础
"""
from .openai_client import OpenAIClient
from ...models.model_schemas import ModelConfig


class DoubaoClient(OpenAIClient):
    """豆包客户端（基于 OpenAI 兼容协议）"""
    
    def __init__(self, config: ModelConfig):
        # 确保使用正确的 endpoint
        if not config.base_url:
            config.base_url = "https://ark.cn-beijing.volces.com/api/v3"
        super().__init__(config)
    
    def chat(self, messages, system_prompt=None, temperature=None, max_tokens=None, **kwargs):
        # 豆包使用 endpoint_id 作为 model 参数
        # 如果用户配置的是模型名而非 endpoint_id，仍然按原样传递
        return super().chat(messages, system_prompt, temperature, max_tokens, **kwargs)
