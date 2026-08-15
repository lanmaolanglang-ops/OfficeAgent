"""
Model Router - 智能路由器
根据任务类型和模型能力选择最合适的模型
"""
from typing import Optional

from ..models.model_schemas import AITaskType, ModelConfig
from .model_manager import ModelManager


class ModelRouter:
    """智能路由器"""
    
    def __init__(self, model_manager: ModelManager):
        self.model_manager = model_manager
    
    def select_model(self, task_type: AITaskType,
                    prefer_model: Optional[str] = None,
                    require_vision: bool = False) -> list[str]:
        """
        选择模型，返回按优先级排序的模型ID列表
        
        Args:
            task_type: 任务类型
            prefer_model: 用户指定的首选模型ID
            require_vision: 是否需要视觉能力
        """
        # 用户指定模型优先
        if prefer_model:
            model = self.model_manager.get_model(prefer_model)
            if model and model.enabled and model.api_key:
                if require_vision and not model.supports_vision:
                    pass  # 指定的模型不支持视觉，继续找其他
                else:
                    # 返回指定模型 + 其他可用模型作为备用
                    routing = self.model_manager.get_routing(task_type)
                    result = [prefer_model]
                    for mid in routing:
                        if mid != prefer_model:
                            result.append(mid)
                    return result
        
        # 按路由策略获取候选
        routing = self.model_manager.get_routing(task_type)
        
        # 过滤可用模型
        available = []
        for model_id in routing:
            config = self.model_manager.get_model(model_id)
            if not config or not config.enabled or not config.api_key:
                continue
            if require_vision and not config.supports_vision:
                continue
            available.append(model_id)
        
        # 如果需要视觉但路由中没有视觉模型，找所有支持视觉的
        if require_vision and not available:
            for model in self.model_manager.list_available_models():
                if model.supports_vision:
                    available.append(model.id)
        
        # 如果没有可用模型，返回所有有 API Key 的模型
        if not available:
            available = [m.id for m in self.model_manager.list_available_models()]
        
        return available
    
    def infer_task_type(self, user_input: str,
                       has_file: bool = False,
                       file_type: Optional[str] = None) -> AITaskType:
        """
        根据用户输入推断 AI 任务类型
        """
        text = user_input.lower()
        
        # 视觉/图片相关
        vision_keywords = ["图片", "照片", "截图", "模板分析", "视觉", "看图",
                          "image", "picture", "photo", "ppt模板", "设计风格"]
        if any(kw in text for kw in vision_keywords):
            return AITaskType.VISION
        
        # 代码生成
        code_keywords = ["代码", "脚本", "python", "函数", "编程", "开发",
                        "code", "script", "programming", "写个工具"]
        if any(kw in text for kw in code_keywords):
            return AITaskType.CODE_GENERATION
        
        # Excel 公式
        formula_keywords = ["公式", "函数", "vlookup", "sum", "计算",
                          "增长率", "占比", "求和", "平均", "excel公式"]
        if any(kw in text for kw in formula_keywords):
            return AITaskType.FORMULA_GENERATION
        
        # 文档理解（长文档/论文分析）
        doc_keywords = ["论文", "分析结构", "理解文档", "长文档", "总结报告",
                       "文档结构", "章节", "大纲"]
        if any(kw in text for kw in doc_keywords) or (has_file and file_type == "docx"):
            return AITaskType.DOCUMENT_UNDERSTANDING
        
        # PPT 内容
        ppt_keywords = ["ppt", "幻灯片", "演示", "汇报", "课件", "大纲",
                       "目录", "封面", "内容页"]
        if any(kw in text for kw in ppt_keywords):
            return AITaskType.PPT_CONTENT
        
        # 中文写作
        writing_keywords = ["写", "生成", "创作", "报告", "总结", "文案",
                          "内容", "润色", "扩写"]
        if any(kw in text for kw in writing_keywords):
            return AITaskType.CHINESE_WRITING
        
        # 默认简单文本
        return AITaskType.SIMPLE_TEXT
    
    def get_recommended_model_info(self, task_type) -> dict:
        """获取推荐模型信息（用于展示）"""
        if isinstance(task_type, str):
            task_type = AITaskType(task_type)
        model_ids = self.select_model(task_type)
        info = {
            "task_type": task_type.value,
            "primary": None,
            "fallbacks": [],
        }
        
        for i, mid in enumerate(model_ids):
            config = self.model_manager.get_model(mid)
            if config:
                model_info = {
                    "id": mid,
                    "name": config.display_name,
                    "provider": config.provider.value,
                    "model": config.model,
                }
                if i == 0:
                    info["primary"] = model_info
                else:
                    info["fallbacks"].append(model_info)
        
        return info
