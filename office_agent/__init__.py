"""
Office Agent - 智能办公自动化（本地桌面版）

支持 Word/PPT/Excel 自动处理，多模型 AI 驱动。
核心能力：
- Word: 文档排版 / 格式规范化（services.WordService）
- PPT:  智能生成演示文稿（ppt_agent.PPTOrchestrator）
- Excel: 数据分析 / 公式 / 图表（excel_agent.ExcelOrchestrator）
- 模型: 多提供商网关 + 故障转移（model_gateway.ModelGateway）
"""

from .models import (
    ProcessResult,
    FormatConfig,
    FontConfig,
    ModelProvider,
    AITaskType,
)
from .model_gateway import ModelGateway
from .services import WordService
from .ppt_agent import PPTOrchestrator, PPTService
from .excel_agent import ExcelOrchestrator, ExcelService

__version__ = "0.51.1"

__all__ = [
    "__version__",
    # 模型网关
    "ModelGateway",
    # 服务与编排
    "WordService",
    "PPTOrchestrator", "PPTService",
    "ExcelOrchestrator", "ExcelService",
    # 通用模型
    "ProcessResult", "FormatConfig", "FontConfig", "ModelProvider", "AITaskType",
]
