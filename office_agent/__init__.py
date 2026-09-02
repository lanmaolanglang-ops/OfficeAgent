"""
Office Agent - 智能办公自动化（本地桌面版）

支持 Word/PPT/Excel 自动处理，多模型 AI 驱动。
核心能力：
- Word: 文档排版 / 格式规范化（services.WordService）
- PPT:  智能生成演示文稿（ppt_agent.PPTOrchestrator）
- Excel: 数据分析 / 公式 / 图表（excel_agent.ExcelOrchestrator）
- 模型: 多提供商网关 + 故障转移（model_gateway.ModelGateway）
"""
from importlib import import_module

from ._version import __version__


_LAZY_EXPORTS = {
    "ProcessResult": (".models", "ProcessResult"),
    "FormatConfig": (".models", "FormatConfig"),
    "FontConfig": (".models", "FontConfig"),
    "ModelProvider": (".models", "ModelProvider"),
    "AITaskType": (".models", "AITaskType"),
    "ModelGateway": (".model_gateway", "ModelGateway"),
    "WordService": (".services", "WordService"),
    "PPTOrchestrator": (".ppt_agent", "PPTOrchestrator"),
    "PPTService": (".ppt_agent", "PPTService"),
    "ExcelOrchestrator": (".excel_agent", "ExcelOrchestrator"),
    "ExcelService": (".excel_agent", "ExcelService"),
}


def __getattr__(name: str):
    """Preserve the public convenience API without importing every subsystem."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value

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
