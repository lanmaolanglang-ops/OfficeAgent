"""
Model Gateway - 模型网关统一入口
提供统一的 AI 调用接口，自动路由 + 故障转移
"""
from typing import Optional, Callable

from ..models.model_schemas import (
    ModelConfig, ModelProvider, ModelResponse, ChatMessage, AITaskType,
    DEFAULT_ROUTING,
)
from .model_manager import ModelManager
from .model_router import ModelRouter
from .failover import FailoverManager


class ModelGateway:
    """
    模型网关 - 统一入口
    
    使用方式:
        gateway = ModelGateway()
        
        # 简单对话
        result = gateway.chat("分析这个文档结构", task_type="document_understanding")
        
        # 指定任务类型
        result = gateway.chat(
            messages=[{"role": "user", "content": "..."}],
            task_type=AITaskType.CODE_GENERATION
        )
        
        # 文档分析
        result = gateway.analyze_document("paper.docx", "识别标题层级")
        
        # 图片分析
        result = gateway.analyze_image("template.png", "分析PPT设计风格")
    """
    
    def __init__(self, config_dir: Optional[str] = None):
        self.manager = ModelManager(config_dir)
        self.router = ModelRouter(self.manager)
        self.failover = FailoverManager(self.manager)
        self.last_call = None
    
    # === 配置管理 ===
    
    def add_provider(self, provider: str, api_key: str,
                    model: str = "", base_url: str = "",
                    display_name: str = "",
                    model_id: Optional[str] = None) -> ModelConfig:
        """
        添加模型提供商
        
        Args:
            provider: 提供商名称 (openai/deepseek/doubao/qwen/claude/gemini/custom)
            api_key: API Key
            model: 模型名称
            base_url: API Base URL
            display_name: 显示名称
            model_id: 自定义模型ID
        """
        provider_enum = ModelProvider(provider)
        
        if not model_id:
            model_id = f"{provider}-custom"
        
        if not display_name:
            display_name = f"{provider} {model}".strip()
        
        # 获取默认配置
        default = self.manager.get_model(f"{provider}-default")
        
        supports_vision = default.supports_vision if default else False
        supports_document = default.supports_document if default else False
        
        if not base_url and default:
            base_url = default.base_url
        
        config = ModelConfig(
            id=model_id,
            provider=provider_enum,
            display_name=display_name,
            api_key=api_key,
            base_url=base_url,
            model=model or (default.model if default else ""),
            enabled=True,
            supports_vision=supports_vision,
            supports_document=supports_document,
        )
        
        self.manager.add_model(config)
        # Newly saved model becomes the display-primary and route-primary.
        manager = self.manager
        manager._models = {
            config.id: config,
            **{mid: mc for mid, mc in manager._models.items() if mid != config.id},
        }
        if not manager._routing:
            for task_type, model_ids in DEFAULT_ROUTING.items():
                manager._routing[task_type.value] = list(model_ids)
        for task_key, model_ids in manager._routing.items():
            if config.id in model_ids:
                model_ids.remove(config.id)
            model_ids.insert(0, config.id)
        manager._save_config()
        return config
    
    def remove_provider(self, model_id: str) -> bool:
        """移除模型配置"""
        return self.manager.remove_model(model_id)
    
    def list_providers(self) -> list[dict]:
        """列出所有已配置的模型"""
        result = []
        for m in self.manager.list_models():
            result.append({
                "id": m.id,
                "name": m.display_name,
                "provider": m.provider.value,
                "model": m.model,
                "enabled": m.enabled,
                "has_key": bool(m.api_key),
                "supports_vision": m.supports_vision,
            })
        return result
    
    def test_connection(self, model_id: str) -> tuple[bool, str]:
        """测试模型连接"""
        return self.manager.test_model(model_id)
    
    def set_routing(self, task_type: str, model_ids: list[str]):
        """设置路由策略"""
        self.manager.set_routing(AITaskType(task_type), model_ids)
    
    def get_routing(self, task_type: Optional[str] = None) -> dict:
        """获取路由策略"""
        if task_type:
            return {
                "task_type": task_type,
                "models": self.manager.get_routing(AITaskType(task_type))
            }
        result = {}
        for tt in AITaskType:
            result[tt.value] = self.manager.get_routing(tt)
        return result
    
    def get_status(self) -> dict:
        """获取网关状态"""
        return {
            "models": self.list_providers(),
            "failover_status": self.failover.get_model_status(),
            "routing": self.get_routing(),
        }
    
    # === AI 调用接口 ===
    
    def chat(self, messages=None,
            user_message: str = "",
            system_prompt: Optional[str] = None,
            task_type: Optional[AITaskType] = None,
            task_type_str: Optional[str] = None,
            prefer_model: Optional[str] = None,
            temperature: Optional[float] = None,
            max_tokens: Optional[int] = None) -> ModelResponse:
        """
        统一聊天接口
        
        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            user_message: 单条用户消息（与messages二选一）
            system_prompt: 系统提示词
            task_type: AI任务类型
            task_type_str: 任务类型字符串
            prefer_model: 指定首选模型ID
            temperature: 温度
            max_tokens: 最大token
        """
        # 解析任务类型
        if task_type_str and not task_type:
            try:
                task_type = AITaskType(task_type_str)
            except ValueError:
                task_type = AITaskType.SIMPLE_TEXT
        
        if not task_type:
            # 自动推断
            input_text = user_message or (messages[-1].get("content", "") if messages else "")
            task_type = self.router.infer_task_type(input_text)
        
        # 构建消息
        if messages is None:
            messages = [{"role": "user", "content": user_message}]
        elif isinstance(messages, list) and messages and isinstance(messages[0], ChatMessage):
            messages = [m.to_dict() for m in messages]
        
        # 选择模型
        model_ids = self.router.select_model(task_type, prefer_model=prefer_model)
        
        # 执行调用（带故障转移）
        def action(client, **kw):
            return client.chat(
                messages=messages,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        
        response = self.failover.execute_with_failover(
            task_type=task_type,
            action=action,
            model_ids=model_ids,
        )
        self.last_call = {
            "called": True,
            "success": bool(response and response.success),
            "model": getattr(response, "model_used", None),
            "provider": getattr(response, "provider", None),
            "fallback_used": bool(response and not response.success),
            "error": getattr(response, "error", "") if response else "no response",
            "attempts": (response.raw_response or {}).get("attempts", 1)
            if response and isinstance(getattr(response, "raw_response", None), dict) else 1,
        }
        return response
    
    def analyze_document(self, file_path: str, prompt: str,
                        system_prompt: Optional[str] = None,
                        prefer_model: Optional[str] = None) -> ModelResponse:
        """文档分析"""
        model_ids = self.router.select_model(
            AITaskType.DOCUMENT_UNDERSTANDING,
            prefer_model=prefer_model
        )
        
        def action(client, **kw):
            return client.analyze_document(file_path, prompt, system_prompt)
        
        return self.failover.execute_with_failover(
            task_type=AITaskType.DOCUMENT_UNDERSTANDING,
            action=action,
            model_ids=model_ids,
        )
    
    def analyze_image(self, image_path: str, prompt: str,
                     system_prompt: Optional[str] = None,
                     prefer_model: Optional[str] = None) -> ModelResponse:
        """图片/视觉分析"""
        model_ids = self.router.select_model(
            AITaskType.VISION,
            prefer_model=prefer_model,
            require_vision=True
        )
        
        def action(client, **kw):
            return client.analyze_image(image_path, prompt, system_prompt)
        
        return self.failover.execute_with_failover(
            task_type=AITaskType.VISION,
            action=action,
            model_ids=model_ids,
        )
    
    def generate_code(self, prompt: str,
                     system_prompt: Optional[str] = None,
                     prefer_model: Optional[str] = None) -> ModelResponse:
        """代码生成"""
        default_system = "你是一个专业的Python开发者，擅长Office自动化编程。只输出代码，不要解释。"
        return self.chat(
            user_message=prompt,
            system_prompt=system_prompt or default_system,
            task_type=AITaskType.CODE_GENERATION,
            prefer_model=prefer_model,
        )
    
    def parse_natural_language(self, text: str, context: str = "",
                              prefer_model: Optional[str] = None) -> ModelResponse:
        """自然语言解析（用于参数提取）"""
        system_prompt = """你是办公自动化参数解析器。根据用户的自然语言描述，提取结构化参数。
以JSON格式返回，不要输出其他内容。"""
        
        prompt = f"上下文: {context}\n用户输入: {text}\n请提取结构化参数:"
        
        return self.chat(
            user_message=prompt,
            system_prompt=system_prompt,
            task_type=AITaskType.SIMPLE_TEXT,
            prefer_model=prefer_model,
            temperature=0.1,
        )
