"""Excel 相关数据库模型"""
import uuid
from sqlalchemy import String, Text, Integer, Boolean, Float, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin


def _excel_template_id():
    return f"excel_tpl_{uuid.uuid4().hex[:12]}"


def _analysis_history_id():
    return f"excel_ana_{uuid.uuid4().hex[:12]}"


def _excel_generation_id():
    return f"excel_gen_{uuid.uuid4().hex[:12]}"


class ExcelTemplate(Base, TimestampMixin):
    """Excel 模板表"""
    __tablename__ = "excel_template"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_excel_template_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="analysis")
    # analysis/sales/financial/inventory/dashboard/report/budget/forecast

    template_path: Mapped[str] = mapped_column(String(512), nullable=True)
    file_id: Mapped[str] = mapped_column(String(32), nullable=True)
    preview_image: Mapped[str] = mapped_column(String(512), nullable=True)

    # 配置 JSON
    config: Mapped[str] = mapped_column(Text, nullable=True)
    # 包含：列定义、公式规则、图表配置、格式化规则、KPI配置等

    # 主题和样式
    theme: Mapped[str] = mapped_column(String(32), default="business", server_default="business")
    # business/financial/analysis/tech/minimal
    style_config: Mapped[str] = mapped_column(Text, nullable=True)

    # 适用场景
    scenario: Mapped[str] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    tags: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array

    # 包含的工作表配置
    sheets_config: Mapped[str] = mapped_column(Text, nullable=True)  # JSON
    # 图表配置
    charts_config: Mapped[str] = mapped_column(Text, nullable=True)  # JSON
    # 公式模板
    formulas_config: Mapped[str] = mapped_column(Text, nullable=True)  # JSON

    # 统计
    usage_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rating: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    version: Mapped[str] = mapped_column(String(32), default="1.0.0", server_default="1.0.0")

    def __repr__(self):
        return f"<ExcelTemplate {self.name} ({self.type})>"


class AnalysisHistory(Base, TimestampMixin):
    """分析历史表 - 保存数据分析记录和结果"""
    __tablename__ = "analysis_history"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_analysis_history_id)
    task_id: Mapped[str] = mapped_column(String(32), nullable=True, index=True)

    # 数据集信息
    dataset_name: Mapped[str] = mapped_column(String(256), nullable=True)
    file_id: Mapped[str] = mapped_column(String(32), nullable=True)
    sheet_name: Mapped[str] = mapped_column(String(128), nullable=True)

    # 分析类型
    analysis_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # descriptive/trend/comparison/ranking/proportion/correlation/aggregation/prediction/quality_check

    # 分析参数
    parameters: Mapped[str] = mapped_column(Text, nullable=True)  # JSON
    # 分析的列
    columns: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array

    # 结果
    result: Mapped[str] = mapped_column(Text, nullable=True)  # JSON
    result_summary: Mapped[str] = mapped_column(Text, nullable=True)
    insights: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array

    # 预测相关
    forecast_periods: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    model_used: Mapped[str] = mapped_column(String(64), nullable=True)
    accuracy: Mapped[float] = mapped_column(Float, nullable=True)

    # 质量检查
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    issues_found: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    issues_fixed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # 执行信息
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(32), default="completed", server_default="completed")
    error: Mapped[str] = mapped_column(Text, nullable=True)

    # 用户
    user_id: Mapped[str] = mapped_column(String(32), nullable=True)

    def __repr__(self):
        return f"<AnalysisHistory {self.analysis_type} ({self.status})>"


class ExcelGeneration(Base, TimestampMixin):
    """Excel 生成/处理记录表"""
    __tablename__ = "excel_generation"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_excel_generation_id)

    # 任务信息
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # parse/clean/analyze/predict/report/format/quality_check/chart
    description: Mapped[str] = mapped_column(Text, nullable=True)

    # 输入
    input_file_id: Mapped[str] = mapped_column(String(32), nullable=True)
    input_path: Mapped[str] = mapped_column(String(512), nullable=True)
    template_id: Mapped[str] = mapped_column(String(32), nullable=True)

    # 输出
    output_file_id: Mapped[str] = mapped_column(String(32), nullable=True)
    output_path: Mapped[str] = mapped_column(String(512), nullable=True)
    sheet_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    row_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # 处理选项
    options: Mapped[str] = mapped_column(Text, nullable=True)  # JSON

    # 质量
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    quality_report: Mapped[str] = mapped_column(Text, nullable=True)

    # 清洗统计
    rows_cleaned: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duplicates_removed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    missing_filled: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # 图表
    charts_generated: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # 状态
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending")
    error: Mapped[str] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # 用户
    user_id: Mapped[str] = mapped_column(String(32), nullable=True)

    def __repr__(self):
        return f"<ExcelGeneration {self.task_type} ({self.status})>"
