"""
Model Router - 智能路由器
根据任务类型和模型能力选择最合适的模型
"""
import re
from typing import Any, Optional

from office_agent.api.routing import PPT_STRONG, WORD_STRONG

from ..models.model_schemas import (
    AITaskType, TASK_CAPABILITY_REQUIREMENTS,
)
from .model_manager import ModelManager

# 产品词表以 office_agent.api.routing 为单一真相源（WORD_STRONG/PPT_STRONG），
# 本层只追加"模型选型"特有的能力词；判定顺序保持历史行为：
# 视觉 → 代码 → 公式 → 文档理解 → PPT 内容 → 写作。
# "大纲"同时属于文档与 PPT 词表，按此顺序归文档理解（单一裁决点，不再各层分裂）。
VISION_KEYWORDS = ("图片", "照片", "截图", "模板分析", "视觉", "看图",
                   "image", "picture", "photo", "ppt模板", "设计风格")
CODE_KEYWORDS = ("代码", "脚本", "python", "函数", "编程", "开发",
                 "code", "script", "programming", "写个工具")
# 公式路由的词表：中文词保持子串匹配（公式/求和/增长率/占比歧义小），
# ASCII 词必须词边界+公式调用语法（"SUM("）匹配——裸子串会把
# "summary"/"assume"（含 sum）、"总结报告"（含 计算/平均 的泛化词）
# 全部误路由到 FORMULA_GENERATION；Excel 语境用 "excel函数" 表达。
FORMULA_KEYWORDS = ("公式", "vlookup", "求和", "增长率", "占比")
FORMULA_PATTERN = re.compile(
    r"公式|excel\s*函数|vlookup|\bsum\s*\(|求和|增长率|占比",
    re.IGNORECASE,
)
# Excel 专属公式信号：先于通用代码词（"函数"）判定，
# "帮我写 excel 函数" 不是编程任务。
EXCEL_FORMULA_PATTERN = re.compile(r"excel\s*函数|vlookup", re.IGNORECASE)
DOCUMENT_KEYWORDS = tuple(dict.fromkeys(WORD_STRONG + (
    "分析结构", "理解文档", "长文档", "总结报告", "文档结构", "章节", "大纲",
)))
PPT_KEYWORDS = tuple(dict.fromkeys(PPT_STRONG + (
    "演示", "汇报", "大纲", "目录", "封面", "内容页",
)))
WRITING_KEYWORDS = ("写", "生成", "创作", "报告", "总结", "文案",
                    "内容", "润色", "扩写")


class ModelRouter:
    """智能路由器"""
    
    def __init__(self, model_manager: ModelManager):
        self.model_manager = model_manager

    @staticmethod
    def _requirements(task_type, require_vision: bool) -> list[tuple[str, bool]]:
        """汇总本次选择需要满足的能力门槛：[(字段名, 是否硬门槛), ...]。

        硬门槛不满足即淘汰；软门槛只影响排序（具备者优先）。
        """
        requirements: list[tuple[str, bool]] = []
        if require_vision:
            requirements.append(("supports_vision", True))
        key = task_type.value if isinstance(task_type, AITaskType) else str(task_type)
        capability = TASK_CAPABILITY_REQUIREMENTS.get(key)
        if capability:
            requirements.append(capability)
        return requirements

    @staticmethod
    def _meets(config, requirements) -> bool:
        return all(
            bool(getattr(config, field, False))
            for field, strict in requirements if strict
        )

    @staticmethod
    def _rank(config, requirements) -> int:
        """软门槛命中数量，越多越靠前。"""
        return sum(
            1 for field, strict in requirements
            if not strict and bool(getattr(config, field, False))
        )
    
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
        # 能力门槛同时来自显式 require_vision 与任务类型本身（P1-8），
        # 这样历史保存在磁盘上的路由表也会在"选型时"被纠正。
        requirements = self._requirements(task_type, require_vision)
        strict = any(s for _f, s in requirements)

        # 用户指定模型优先
        if prefer_model:
            model = self.model_manager.get_model(prefer_model)
            if model and model.enabled and model.api_key:
                if not self._meets(model, requirements):
                    pass  # 指定的模型不满足硬能力门槛，继续找其他
                else:
                    # 返回指定模型 + 其他可用模型作为备用
                    routing = self.model_manager.get_routing(task_type)
                    result = [prefer_model]
                    for mid in routing:
                        if mid == prefer_model:
                            continue
                        candidate = self.model_manager.get_model(mid)
                        if not candidate or not candidate.enabled or not candidate.api_key:
                            continue
                        if not self._meets(candidate, requirements):
                            continue
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
            if not self._meets(config, requirements):
                continue
            available.append(model_id)
        
        # 如果需要视觉但路由中没有视觉模型，找所有支持视觉的
        if strict and not available:
            for model in self.model_manager.list_available_models():
                if self._meets(model, requirements) and model.id not in available:
                    available.append(model.id)
        
        # 视觉任务绝不能退化到纯文本模型；宁可明确返回“无可用模型”。
        if strict and not available:
            return []

        # 软门槛：具备能力的模型排在前面（稳定排序，保持路由原有次序）
        if requirements and available:
            ranks = {}
            for mid in available:
                cfg = self.model_manager.get_model(mid)
                ranks[mid] = self._rank(cfg, requirements) if cfg else 0
            available.sort(key=lambda mid: -ranks[mid])
        
        # 如果没有可用模型，返回所有有 API Key 的模型
        if not available:
            available = [m.id for m in self.model_manager.list_available_models()]
        
        return available
    
    def infer_task_type(self, user_input: Any,
                       has_file: bool = False,
                       file_type: Optional[str] = None) -> AITaskType:
        """
        根据用户输入推断 AI 任务类型
        """
        if isinstance(user_input, str):
            text = user_input
        elif isinstance(user_input, (list, tuple)):
            parts = []
            for message in user_input:
                content = (
                    message.get("content", "")
                    if isinstance(message, dict)
                    else getattr(message, "content", "")
                )
                if isinstance(content, str):
                    parts.append(content)
            text = "\n".join(parts)
        else:
            text = str(user_input or "")
        text = text.lower()
        
        # 视觉/图片相关
        if any(kw in text for kw in VISION_KEYWORDS):
            return AITaskType.VISION

        # 代码生成（Excel 专属公式信号先于通用"函数"词判定）
        if EXCEL_FORMULA_PATTERN.search(text):
            return AITaskType.FORMULA_GENERATION
        if any(kw in text for kw in CODE_KEYWORDS):
            return AITaskType.CODE_GENERATION

        # Excel 公式
        if FORMULA_PATTERN.search(text):
            return AITaskType.FORMULA_GENERATION

        # 文档理解（长文档/论文分析）
        if any(kw in text for kw in DOCUMENT_KEYWORDS) or (has_file and file_type == "docx"):
            return AITaskType.DOCUMENT_UNDERSTANDING

        # PPT 内容
        if any(kw in text for kw in PPT_KEYWORDS):
            return AITaskType.PPT_CONTENT

        # 中文写作
        if any(kw in text for kw in WRITING_KEYWORDS):
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
