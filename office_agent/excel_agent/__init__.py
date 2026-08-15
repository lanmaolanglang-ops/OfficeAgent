"""
Excel Agent - 智能 Excel 处理模块

架构：
用户需求/文件 → ExcelOrchestrator → DataAnalyzer → FormulaGenerator/ChartGenerator
                                                    ↓
                                              ExcelService → .xlsx
                                                    ↓
                                              QualityChecker
"""

from .models import (
    TaskType,
    ChartType,
    CellDataType,
    SemanticType,
    ColumnInfo,
    SheetInfo,
    DataProfile,
    DataSchema,
    SheetSchema,
    DataRelation,
    FormulaSpec,
    ChartSpec,
    FormatSpec,
    ExcelTask,
    ExcelResult,
    ExcelQualityIssue,
    AnalysisReport,
    AnalysisFinding,
    ColumnAnalysis,
    GroupAnalysis,
    TrendAnalysis,
    FindingType,
    FindingSeverity,
)
from .excel_service import ExcelService
from .data_analyzer import DataAnalyzer, analyze_excel, analyze_schema
from .formula_generator import FormulaGenerator, generate_formulas, apply_formulas
from .chart_generator import (
    ChartGenerator,
    generate_charts,
    auto_charts_from_file,
    generate_charts_to_file,
)
from .analysis_engine import AnalysisEngine, analyze_excel as analyze_excel_data, analyze_data
from .template_analyzer import (
    ExcelTemplateAnalyzer,
    ExcelTemplateConfig,
    SheetTemplate,
    ColumnTemplate,
    CellStyleInfo,
    BorderStyleInfo,
    analyze_excel_template,
    fill_template,
)
from .quality_checker import (
    ExcelQualityChecker,
    QualityReport,
    check_excel,
    check_and_fix_excel,
)
from .excel_orchestrator import ExcelOrchestrator
from .vision_analyzer import (
    ExcelVisionAnalyzer,
    ExcelVisionResult,
    RecognizedTable,
    RecognizedColumn,
    SuggestedAnalysis,
    TableType,
    ColumnSemanticType,
    AnalysisType,
    ChartSuggestion,
    analyze_excel_screenshot,
)

__all__ = [
    # 模型
    "TaskType", "ChartType", "CellDataType", "SemanticType",
    "ColumnInfo", "SheetInfo", "DataProfile",
    "DataSchema", "SheetSchema", "DataRelation",
    "FormulaSpec", "ChartSpec", "FormatSpec",
    "ExcelTask", "ExcelResult", "ExcelQualityIssue",
    "AnalysisReport", "AnalysisFinding", "ColumnAnalysis",
    "GroupAnalysis", "TrendAnalysis", "FindingType", "FindingSeverity",
    # 服务
    "ExcelService",
    "DataAnalyzer", "analyze_excel", "analyze_schema",
    "FormulaGenerator", "generate_formulas", "apply_formulas",
    "ChartGenerator", "generate_charts",
    "auto_charts_from_file", "generate_charts_to_file",
    "AnalysisEngine", "analyze_excel_data", "analyze_data",
    "ExcelTemplateAnalyzer", "ExcelTemplateConfig",
    "SheetTemplate", "ColumnTemplate", "CellStyleInfo", "BorderStyleInfo",
    "analyze_excel_template", "fill_template",
    "ExcelQualityChecker", "QualityReport",
    "check_excel", "check_and_fix_excel",
    "ExcelOrchestrator",
    # Vision Analyzer
    "ExcelVisionAnalyzer", "ExcelVisionResult",
    "RecognizedTable", "RecognizedColumn", "SuggestedAnalysis",
    "TableType", "ColumnSemanticType", "AnalysisType", "ChartSuggestion",
    "analyze_excel_screenshot",
]
