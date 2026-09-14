"""
Excel Analysis Engine - 数据分析引擎

自动发现：
- 最大值/最小值/平均值/中位数/总计
- 增长趋势（环比/同比/整体趋势）
- 异常数据（离群值、异常波动）
- 排名（Top N / Bottom N）
- 分组对比（按维度聚合）
- 集中度分析

输出：结构化分析报告
"""
import logging
import re
import statistics
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, TypedDict
from collections import defaultdict

from .models import (
    AnalysisReport, AnalysisFinding, ColumnAnalysis, GroupAnalysis,
    TrendAnalysis, FindingType, FindingSeverity,
    DataProfile, DataSchema,
)

logger = logging.getLogger("office_agent.excel_agent.analysis_engine")

# 趋势阈值（此前一处 0.03、一处 0.05 硬编码且口径不明）：
# - 方向判定：平均环比绝对值超过 3% 才认为存在方向性趋势，避免噪声被命名为涨/跌
# - 重要发现：平均环比绝对值超过 5% 才升级为“重要”发现推送，低于该值只标记趋势
TREND_DIRECTION_THRESHOLD = 0.03
TREND_FINDING_THRESHOLD = 0.05

# 列类型嗅探采样行数：只看前 N 个非空值判定 date/number/text，
# 避免大表全量扫描；采样需足够多以降低误判，50 为经验值。
TYPE_DETECTION_SAMPLE_SIZE = 50


class _GroupAccumulator(TypedDict):
    """分组聚合累加器：维度分组内指标求和、计数与原始值集合。"""
    sum: float
    count: int
    values: list[float]


class AnalysisEngine:
    """
    Excel 数据分析引擎

    用法:
        engine = AnalysisEngine()
        report = engine.analyze_file("销售数据.xlsx")
        print(report.to_text())
        report.save("report.txt")
    """

    def __init__(self):
        self.raw_data: Dict[str, List[List]] = {}  # sheet_name -> rows
        self.headers: Dict[str, List[str]] = {}    # sheet_name -> headers
        self.profile: Optional[DataProfile] = None
        self.schema: Optional[DataSchema] = None

    def analyze_file(self, file_path: str,
                     sheet_name: str | None = None) -> AnalysisReport:
        """分析 Excel 文件，生成完整报告"""
        # 1. 使用公式缓存值读取数据。data_only=False 会把公式字符串当文本，
        # 使包含公式的数值列被静默排除。
        from openpyxl import load_workbook
        wb_values = load_workbook(file_path, data_only=True, read_only=True)
        try:
            requested_sheets = [sheet_name] if sheet_name else list(wb_values.sheetnames)
            missing = [name for name in requested_sheets if name not in wb_values.sheetnames]
            if missing:
                raise KeyError(f"工作表不存在: {missing[0]}")
            for current_sheet in requested_sheets:
                self._read_sheet_data(wb_values[current_sheet], current_sheet)
        finally:
            wb_values.close()

        # 2. 分析数据画像。全簿读取时复用第 1 步已载入内存的数据，
        # 不再对同一文件做第二次解析；指定单表时 schema 语义覆盖全簿，
        # 保留文件级入口（清单：analyze_file 重复加载）。
        try:
            from .data_analyzer import DataAnalyzer
            analyzer = DataAnalyzer()
            if sheet_name is None:
                import pandas as pd
                frames = {
                    name: pd.DataFrame(
                        self.raw_data.get(name, []),
                        columns=self.headers.get(name) or None,
                    )
                    for name in requested_sheets
                }
                self.schema = analyzer.analyze_schema_frames(file_path, frames)
            else:
                self.schema = analyzer.analyze_schema(file_path)
        except Exception as exc:
            # 画像失败不阻断主分析：降级为无 schema 继续生成报告，但原因必须可观测
            logger.warning(
                "数据画像失败，降级为无 schema 继续分析 %s: %s",
                file_path, exc, exc_info=True,
            )
            self.schema = None

        reports = [self._run_analysis(file_path, name) for name in requested_sheets]
        if len(reports) == 1:
            return reports[0]

        combined = AnalysisReport(
            file_path=file_path,
            file_name=reports[0].file_name,
            sheet_name="全部工作表",
            analysis_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            total_rows=sum(report.total_rows for report in reports),
            total_columns=sum(report.total_columns for report in reports),
            sheet_reports=reports,
        )
        combined.findings = [finding for report in reports for finding in report.findings]
        combined.summary = "；".join(
            f"{report.sheet_name}: {report.summary}" for report in reports if report.summary
        )
        combined.key_findings = "\n".join(
            f"[{report.sheet_name}] {finding.title}"
            for report in reports for finding in report.findings[:5]
        )
        combined.recommendations = "\n".join(
            f"[{report.sheet_name}] {report.recommendations}"
            for report in reports if report.recommendations
        )
        return combined

    def analyze_data(self, data: List[List], headers: List[str] | None = None,
                     sheet_name: str = "Sheet1",
                     file_name: str = "data.xlsx") -> AnalysisReport:
        """直接从数据（二维列表）分析"""
        if headers is None:
            headers = [f"列{i+1}" for i in range(len(data[0]) if data else 0)]
            self.raw_data[sheet_name] = data
        else:
            self.raw_data[sheet_name] = data
        self.headers[sheet_name] = headers

        return self._run_analysis(file_name, sheet_name)

    # ==========================================
    # 核心分析流程
    # ==========================================

    def _run_analysis(self, file_path: str, sheet_name: str) -> AnalysisReport:
        """执行完整分析"""
        report = AnalysisReport(
            file_path=file_path,
            file_name=file_path.split("/")[-1].split("\\")[-1] if file_path else "data.xlsx",
            sheet_name=sheet_name,
            analysis_time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        )

        rows = self.raw_data.get(sheet_name, [])
        headers = self.headers.get(sheet_name, [])

        if not rows:
            report.summary = "数据为空，无法分析"
            return report

        report.total_rows = len(rows)
        report.total_columns = len(headers) if headers else len(rows[0]) if rows else 0

        # 识别列类型
        col_types = self._detect_column_types(rows, headers)
        numeric_cols = [i for i, t in enumerate(col_types) if t == "number"]
        text_cols = [i for i, t in enumerate(col_types) if t == "text"]
        date_cols = [i for i, t in enumerate(col_types) if t == "date"]

        # 1. 逐列统计分析
        for col_idx in numeric_cols:
            col_analysis = self._analyze_column(
                rows, headers, col_idx, text_cols, sheet_name=sheet_name
            )
            report.column_analyses.append(col_analysis)

        # 2. 趋势分析（有时间/有序列时）
        time_col = self._find_time_column(headers, date_cols, text_cols)
        for col_idx in numeric_cols:
            trend = self._analyze_trend(rows, headers, col_idx, time_col)
            if trend and trend.period_count > 1:
                report.trend_analyses.append(trend)

        # 3. 分组分析（有分类列时）
        for dim_col in text_cols[:3]:  # 最多3个维度
            for metric_col in numeric_cols[:5]:  # 最多5个指标
                group = self._analyze_group(rows, headers, dim_col, metric_col)
                if group and len(group.groups) >= 2:
                    report.group_analyses.append(group)

        # 4. 生成发现
        report.findings = self._generate_findings(report, headers)

        # 5. 生成文本报告
        report.summary = self._generate_summary(report)
        report.key_findings = "\n".join(
            f.title for f in report.findings[:10]
        )
        report.recommendations = self._generate_recommendations(report)

        return report

    # ==========================================
    # 数据读取
    # ==========================================

    def _read_sheet_data(self, ws, sheet_name: str):
        """从工作表读取数据"""
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append(list(row))

        if not rows:
            self.raw_data[sheet_name] = []
            self.headers[sheet_name] = []
            return

        # 第一行作为表头
        headers = [str(h) if h is not None else f"列{i+1}" for i, h in enumerate(rows[0])]
        data = rows[1:]

        self.headers[sheet_name] = headers
        self.raw_data[sheet_name] = data

    # ==========================================
    # 列类型检测
    # ==========================================

    def _detect_column_types(self, rows: List[List],
                              headers: List[str]) -> List[str]:
        """检测每列的数据类型"""
        if not rows:
            return []

        n_cols = len(headers) if headers else len(rows[0])
        types = []

        for i in range(n_cols):
            values = [row[i] for row in rows if i < len(row) and row[i] is not None]
            if not values:
                types.append("text")
                continue

            # 尝试数字
            num_count = 0
            date_count = 0
            for v in values[:TYPE_DETECTION_SAMPLE_SIZE]:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    num_count += 1
                elif isinstance(v, datetime):
                    date_count += 1
                else:
                    try:
                        number = self._to_float(v)
                        if number is None:
                            raise ValueError
                        num_count += 1
                    except (ValueError, TypeError):
                        pass

            if date_count > len(values[:TYPE_DETECTION_SAMPLE_SIZE]) * 0.5:
                types.append("date")
            elif num_count > len(values[:TYPE_DETECTION_SAMPLE_SIZE]) * 0.5:
                types.append("number")
            else:
                types.append("text")

        return types

    def _get_numeric_values(self, rows: List[List], col_idx: int) -> List[float]:
        """获取列的数值列表"""
        values = []
        for row in rows:
            if col_idx < len(row) and row[col_idx] is not None:
                v = row[col_idx]
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    values.append(float(v))
                else:
                    number = self._to_float(v)
                    if number is not None:
                        values.append(number)
        return values

    def _get_numeric_points(self, rows: List[List], col_idx: int) -> List[Tuple[int, float]]:
        """返回源行索引和数值，避免过滤空值后与时间标签错位。"""
        points = []
        for row_index, row in enumerate(rows):
            if col_idx >= len(row) or row[col_idx] is None:
                continue
            value = row[col_idx]
            try:
                if isinstance(value, bool):
                    continue
                number = self._to_float(value)
                if number is None:
                    continue
            except (TypeError, ValueError):
                continue
            points.append((row_index, number))
        return points

    def _find_time_column(self, headers: List[str],
                           date_cols: List[int],
                           text_cols: List[int]) -> int:
        """查找时间列"""
        # 优先日期列
        if date_cols:
            return date_cols[0]

        # 通过列名识别
        exact_keywords = {"期", "周期", "周", "月份", "季度", "年度", "日期", "时间"}
        token_pattern = re.compile(
            r"(?:^|[_\-\s])(month|year|date|quarter|week|period)(?:$|[_\-\s])",
            re.IGNORECASE,
        )
        for i, h in enumerate(headers):
            header = str(h).strip()
            if (header in exact_keywords
                    or any(word in header for word in ("日期", "时间", "月份", "季度", "年度"))
                    or token_pattern.search(header)):
                return i

        # P3-31: without an explicit time column, only the leading text column
        # is a defensible X/time axis (conventional layout). Picking an interior
        # text column would mislabel a name/category column as the time axis.
        return text_cols[0] if text_cols and text_cols[0] == 0 else -1

    # ==========================================
    # 单列统计分析
    # ==========================================

    def _analyze_column(self, rows: List[List], headers: List[str],
                         col_idx: int, text_cols: List[int],
                         sheet_name: str | None = None) -> ColumnAnalysis:
        """分析单列"""
        col_name = headers[col_idx] if col_idx < len(headers) else f"列{col_idx+1}"
        values = self._get_numeric_values(rows, col_idx)

        ca = ColumnAnalysis(
            column_name=col_name,
            data_type="number",
            count=len(values),
        )

        # 从 schema 获取语义类型和单位
        if self.schema and self.schema.sheets:
            sheet = self.schema.get_sheet(sheet_name)
            if sheet and col_idx < len(sheet.columns):
                col_info = sheet.columns[col_idx]
                ca.semantic_type = col_info.semantic_type or ""
                ca.unit = col_info.unit or ""

        if not values:
            return ca

        ca.sum_value = sum(values)
        ca.avg_value = statistics.mean(values)
        ca.median_value = statistics.median(values)
        ca.max_value = max(values)
        ca.min_value = min(values)

        if len(values) > 1:
            ca.std_dev = statistics.stdev(values)
            if ca.avg_value != 0:
                ca.cv = ca.std_dev / abs(ca.avg_value)

        # 最大/最小值对应的标签
        label_col = self._find_label_column(headers, text_cols, col_idx)
        if label_col >= 0:
            max_label = self._find_label_for_value(rows, col_idx, ca.max_value, label_col)
            min_label = self._find_label_for_value(rows, col_idx, ca.min_value, label_col)
            ca.max_label = max_label
            ca.min_label = min_label

        # Top/Bottom 5
        labeled_values = []
        for row in rows:
            if col_idx < len(row):
                v = self._to_float(row[col_idx])
                if v is not None:
                    label = str(row[label_col]) if label_col >= 0 and label_col < len(row) else ""
                    labeled_values.append((label, v))

        labeled_values.sort(key=lambda x: x[1], reverse=True)
        ca.top_values = labeled_values[:5]
        ca.bottom_values = labeled_values[-5:][::-1]

        # 整体增长率（首末对比）
        if len(values) >= 2 and values[0] != 0:
            ca.growth_rate = (values[-1] - values[0]) / abs(values[0])

        # 趋势判断
        if len(values) >= 3:
            first_half = statistics.mean(values[:len(values)//2])
            second_half = statistics.mean(values[len(values)//2:])
            if second_half > first_half * 1.05:
                ca.trend = "up"
            elif second_half < first_half * 0.95:
                ca.trend = "down"
            else:
                ca.trend = "stable"

        # 异常值检测（IQR方法）
        ca.outliers = self._detect_outliers_iqr(values, rows, col_idx, label_col)

        return ca

    def _find_label_column(self, headers: List[str],
                            text_cols: List[int],
                            exclude_col: int) -> int:
        """查找标签列（用于标识最大最小值）"""
        # 优先名称类列
        name_keywords = ["名称", "姓名", "名字", "地区", "城市", "省份",
                        "产品", "部门", "月份", "季度", "name", "region",
                        "product", "city", "month", "year"]
        for i in text_cols:
            if i == exclude_col:
                continue
            h = str(headers[i]).lower() if i < len(headers) else ""
            for kw in name_keywords:
                if kw in h:
                    return i

        # 第一个文本列
        for i in text_cols:
            if i != exclude_col:
                return i

        return -1

    def _find_label_for_value(self, rows: List[List], value_col: int,
                               target_value: float, label_col: int) -> str:
        """查找特定值对应的标签"""
        for row in rows:
            if value_col < len(row) and label_col < len(row):
                v = self._to_float(row[value_col])
                if v is not None and abs(v - target_value) < 0.001:
                    return str(row[label_col]) if row[label_col] else ""
        return ""

    def _detect_outliers_iqr(self, values: List[float], rows: List[List],
                              col_idx: int, label_col: int) -> list:
        """使用 IQR 方法检测离群值"""
        if len(values) < 4:
            return []

        sorted_vals = sorted(values)
        # inclusive 分位数对小样本做线性插值，避免位次法直接把端点
        # 当作四分位数而漏报明显离群值。
        q1, _, q3 = statistics.quantiles(sorted_vals, n=4, method="inclusive")
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr

        outliers = []
        for row in rows:
            if col_idx < len(row):
                v = self._to_float(row[col_idx])
                if v is not None and (v < lower or v > upper):
                    label = str(row[label_col]) if label_col >= 0 and label_col < len(row) else ""
                    outliers.append({
                        "value": v,
                        "label": label,
                        "type": "high" if v > upper else "low",
                    })
        return outliers

    # ==========================================
    # 趋势分析
    # ==========================================

    def _analyze_trend(self, rows: List[List], headers: List[str],
                        col_idx: int, time_col: int) -> Optional[TrendAnalysis]:
        """分析时间序列趋势"""
        points = self._get_numeric_points(rows, col_idx)
        if len(points) < 2:
            return None
        values = [value for _, value in points]

        col_name = headers[col_idx] if col_idx < len(headers) else f"列{col_idx+1}"
        time_name = headers[time_col] if 0 <= time_col < len(headers) else ""

        ta = TrendAnalysis(
            column_name=col_name,
            time_column=time_name,
            period_count=len(values),
        )

        # 逐期环比
        growth_rates = []
        growth_periods = []
        for i in range(1, len(values)):
            if values[i-1] != 0:
                rate = (values[i] - values[i-1]) / abs(values[i-1])
                growth_rates.append(rate)

                source_row = points[i][0]
                period_label = f"第{i+1}期"
                if 0 <= source_row < len(rows) and 0 <= time_col < len(rows[source_row]):
                    period_label = str(rows[source_row][time_col])
                growth_periods.append(period_label)

                ta.periods.append({
                    "period": period_label,
                    "value": values[i],
                    "prev_value": values[i-1],
                    "change_rate": rate,
                })

        if growth_rates:
            ta.avg_growth_rate = statistics.mean(growth_rates)

            # 最大增长/下降期
            max_growth_idx = max(range(len(growth_rates)), key=lambda i: growth_rates[i])
            max_decline_idx = min(range(len(growth_rates)), key=lambda i: growth_rates[i])

            ta.max_growth_period = growth_periods[max_growth_idx]
            ta.max_growth_rate = growth_rates[max_growth_idx]

            ta.max_decline_period = growth_periods[max_decline_idx]
            ta.max_decline_rate = growth_rates[max_decline_idx]

        # 整体增长率
        if values[0] != 0:
            ta.growth_rate = (values[-1] - values[0]) / abs(values[0])

        # 趋势判断
        if ta.avg_growth_rate > TREND_DIRECTION_THRESHOLD:
            ta.trend = "up"
        elif ta.avg_growth_rate < -TREND_DIRECTION_THRESHOLD:
            ta.trend = "down"
        elif len(growth_rates) > 2 and max(growth_rates) - min(growth_rates) > 0.2:
            ta.trend = "fluctuating"
        else:
            ta.trend = "stable"

        return ta

    # ==========================================
    # 分组分析
    # ==========================================

    def _analyze_group(self, rows: List[List], headers: List[str],
                        dim_col: int, metric_col: int) -> Optional[GroupAnalysis]:
        """按维度分组分析"""
        if dim_col == metric_col:
            return None

        dim_name = headers[dim_col] if dim_col < len(headers) else f"列{dim_col+1}"
        metric_name = headers[metric_col] if metric_col < len(headers) else f"列{metric_col+1}"

        # 分组聚合
        groups: defaultdict[str, _GroupAccumulator] = defaultdict(
            lambda: {"sum": 0.0, "count": 0, "values": []}
        )
        for row in rows:
            if dim_col < len(row) and metric_col < len(row):
                key = str(row[dim_col]) if row[dim_col] is not None else "(空)"
                v = self._to_float(row[metric_col])
                if v is not None:
                    groups[key]["sum"] += v
                    groups[key]["count"] += 1
                    groups[key]["values"].append(v)

        if len(groups) < 2:
            return None

        total_sum = sum(g["sum"] for g in groups.values())

        group_list = []
        for key, g in groups.items():
            avg = g["sum"] / g["count"] if g["count"] > 0 else 0
            share = g["sum"] / total_sum if total_sum != 0 else 0
            group_list.append((key, g["sum"], avg, g["count"], share))

        group_list.sort(key=lambda x: x[1], reverse=True)

        ga = GroupAnalysis(
            dimension=dim_name,
            metric=metric_name,
            groups=group_list,
        )

        if group_list:
            ga.top_group = group_list[0][0]
            ga.top_value = group_list[0][1]
            ga.top_share = group_list[0][4]
            ga.bottom_group = group_list[-1][0]
            ga.bottom_value = group_list[-1][1]

            # CR3 集中度
            top3_sum = sum(g[1] for g in group_list[:3])
            ga.concentration = top3_sum / total_sum if total_sum != 0 else 0

        return ga

    # ==========================================
    # 发现生成
    # ==========================================

    def _generate_findings(self, report: AnalysisReport,
                            headers: List[str]) -> List[AnalysisFinding]:
        """生成所有分析发现"""
        findings = []

        # 1. 指标极值发现
        for ca in report.column_analyses:
            if ca.semantic_type in ("amount", "quantity", "metric") or ca.count > 0:
                # 最大值
                if ca.max_value is not None and ca.max_label:
                    findings.append(AnalysisFinding(
                        finding_type=FindingType.MAX.value,
                        severity=FindingSeverity.NOTABLE.value,
                        title=f"{ca.max_label}{ca.column_name}最高，为{self._fmt_num(ca.max_value)}{ca.unit}",
                        description=f"{ca.column_name}最大值出现在{ca.max_label}，"
                                   f"达到{self._fmt_num(ca.max_value)}{ca.unit}，"
                                   f"平均值为{self._fmt_num(ca.avg_value)}{ca.unit}",
                        column_name=ca.column_name,
                        dimension_value=ca.max_label,
                        value=ca.max_value,
                        unit=ca.unit,
                    ))

                # 最小值
                if ca.min_value is not None and ca.min_label and ca.min_label != ca.max_label:
                    findings.append(AnalysisFinding(
                        finding_type=FindingType.MIN.value,
                        severity=FindingSeverity.INFO.value,
                        title=f"{ca.min_label}{ca.column_name}最低，为{self._fmt_num(ca.min_value)}{ca.unit}",
                        description=f"{ca.column_name}最小值出现在{ca.min_label}，"
                                   f"为{self._fmt_num(ca.min_value)}{ca.unit}",
                        column_name=ca.column_name,
                        dimension_value=ca.min_label,
                        value=ca.min_value,
                        unit=ca.unit,
                    ))

                # 异常值
                for outlier in ca.outliers[:3]:
                    if outlier["type"] == "high":
                        findings.append(AnalysisFinding(
                            finding_type=FindingType.ANOMALY_HIGH.value,
                            severity=FindingSeverity.IMPORTANT.value,
                            title=f"{outlier['label']}{ca.column_name}异常偏高：{self._fmt_num(outlier['value'])}{ca.unit}",
                            description="该值显著高于正常范围（Q3+1.5IQR），建议核实数据",
                            column_name=ca.column_name,
                            dimension_value=outlier["label"],
                            value=outlier["value"],
                            unit=ca.unit,
                        ))
                    else:
                        findings.append(AnalysisFinding(
                            finding_type=FindingType.ANOMALY_LOW.value,
                            severity=FindingSeverity.NOTABLE.value,
                            title=f"{outlier['label']}{ca.column_name}异常偏低：{self._fmt_num(outlier['value'])}{ca.unit}",
                            column_name=ca.column_name,
                            dimension_value=outlier["label"],
                            value=outlier["value"],
                            unit=ca.unit,
                        ))

        # 2. 趋势发现
        for ta in report.trend_analyses:
            if ta.trend == "up" and ta.avg_growth_rate > TREND_FINDING_THRESHOLD:
                findings.append(AnalysisFinding(
                    finding_type=FindingType.TREND_UP.value,
                    severity=FindingSeverity.IMPORTANT.value,
                    title=f"{ta.column_name}呈上升趋势，平均环比{ta.avg_growth_rate:+.1%}",
                    description=f"从{ta.periods[0]['period'] if ta.periods else '首期'}到"
                               f"{ta.periods[-1]['period'] if ta.periods else '末期'}，"
                               f"整体增长{ta.growth_rate:+.1%}",
                    column_name=ta.column_name,
                    change_rate=ta.avg_growth_rate,
                ))
            elif ta.trend == "down" and ta.avg_growth_rate < -TREND_FINDING_THRESHOLD:
                findings.append(AnalysisFinding(
                    finding_type=FindingType.TREND_DOWN.value,
                    severity=FindingSeverity.IMPORTANT.value,
                    title=f"{ta.column_name}呈下降趋势，平均环比{ta.avg_growth_rate:+.1%}",
                    description=f"整体下降{abs(ta.growth_rate):.1%}，需关注原因",
                    column_name=ta.column_name,
                    change_rate=ta.avg_growth_rate,
                ))

            # 最大增长期
            if ta.max_growth_rate > 0.1:
                findings.append(AnalysisFinding(
                    finding_type=FindingType.GROWTH.value,
                    severity=FindingSeverity.NOTABLE.value,
                    title=f"{ta.max_growth_period}{ta.column_name}增长{ta.max_growth_rate:+.1%}",
                    description="环比增幅最大的时期",
                    column_name=ta.column_name,
                    dimension_value=ta.max_growth_period,
                    change_rate=ta.max_growth_rate,
                ))

            # 最大下降期
            if ta.max_decline_rate < -0.1:
                findings.append(AnalysisFinding(
                    finding_type=FindingType.DECLINE.value,
                    severity=FindingSeverity.NOTABLE.value,
                    title=f"{ta.max_decline_period}{ta.column_name}下降{abs(ta.max_decline_rate):.1%}",
                    description="环比降幅最大的时期，建议分析原因",
                    column_name=ta.column_name,
                    dimension_value=ta.max_decline_period,
                    change_rate=ta.max_decline_rate,
                ))

        # 3. 分组发现
        for ga in report.group_analyses:
            if ga.top_group and ga.top_value is not None:
                findings.append(AnalysisFinding(
                    finding_type=FindingType.RANK_TOP.value,
                    severity=FindingSeverity.NOTABLE.value,
                    title=f"{ga.top_group}{ga.metric}最高，为{self._fmt_num(ga.top_value)}",
                    description=f"按{ga.dimension}分组，{ga.top_group}排名第一，"
                               f"占比{ga.top_share:.1%}",
                    column_name=ga.metric,
                    dimension=ga.dimension,
                    dimension_value=ga.top_group,
                    value=ga.top_value,
                    rank=1,
                ))

            if ga.bottom_group and ga.bottom_value is not None and ga.bottom_group != ga.top_group:
                findings.append(AnalysisFinding(
                    finding_type=FindingType.RANK_BOTTOM.value,
                    severity=FindingSeverity.INFO.value,
                    title=f"{ga.bottom_group}{ga.metric}最低，为{self._fmt_num(ga.bottom_value)}",
                    column_name=ga.metric,
                    dimension=ga.dimension,
                    dimension_value=ga.bottom_group,
                    value=ga.bottom_value,
                    rank=len(ga.groups),
                ))

            # 集中度
            if ga.concentration > 0.7:
                findings.append(AnalysisFinding(
                    finding_type=FindingType.CONCENTRATION.value,
                    severity=FindingSeverity.NOTABLE.value,
                    title=f"{ga.metric}高度集中，Top3占比{ga.concentration:.1%}",
                    description=f"按{ga.dimension}分组，前3名占据大部分份额",
                    column_name=ga.metric,
                    dimension=ga.dimension,
                ))

        # 按重要性排序
        severity_order = {"critical": 0, "important": 1, "notable": 2, "info": 3}
        findings.sort(key=lambda f: severity_order.get(f.severity, 9))

        return findings

    # ==========================================
    # 文本报告生成
    # ==========================================

    def _generate_summary(self, report: AnalysisReport) -> str:
        """生成概要"""
        parts = []
        parts.append(f"共分析{report.total_rows}条记录，{report.total_columns}个字段。")

        # 最关键的发现
        important = [f for f in report.findings if f.severity in ("critical", "important")]
        if important:
            parts.append(f"发现{len(important)}个重要洞察：")
            for f in important[:3]:
                parts.append(f"- {f.title}")

        # 总体趋势
        up_trends = [t for t in report.trend_analyses if t.trend == "up"]
        down_trends = [t for t in report.trend_analyses if t.trend == "down"]
        if up_trends:
            parts.append(f"{'、'.join(t.column_name for t in up_trends)}呈上升趋势。")
        if down_trends:
            parts.append(f"{'、'.join(t.column_name for t in down_trends)}呈下降趋势，需关注。")

        return " ".join(parts)

    def _generate_recommendations(self, report: AnalysisReport) -> str:
        """生成建议"""
        recs = []

        # 下降趋势建议
        for ta in report.trend_analyses:
            if ta.trend == "down":
                recs.append(f"建议分析{ta.column_name}下降原因，重点关注{ta.max_decline_period}前后的变化。")

        # 异常值建议
        anomaly_count = sum(1 for f in report.findings if "anomaly" in f.finding_type)
        if anomaly_count > 0:
            recs.append(f"发现{anomaly_count}个异常数据点，建议核实数据准确性。")

        # 集中度建议
        for ga in report.group_analyses:
            if ga.concentration > 0.8:
                recs.append(f"{ga.metric}在{ga.dimension}上高度集中，建议关注风险分散。")

        if not recs:
            recs.append("数据整体表现平稳，建议持续监控关键指标变化。")

        return "\n".join(f"- {r}" for r in recs)

    # ==========================================
    # 工具方法
    # ==========================================

    @staticmethod
    def _to_float(v) -> Optional[float]:
        """安全转换为浮点数"""
        if v is None:
            return None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        try:
            s = str(v).replace(",", "").strip()
            if s:
                is_percent = s.endswith("%")
                if is_percent:
                    s = s[:-1].strip()
                number = float(s)
                return number / 100.0 if is_percent else number
        except (ValueError, TypeError):
            pass
        return None

    @staticmethod
    def _fmt_num(v, decimals: int = 2) -> str:
        """格式化数字"""
        if v is None:
            return ""
        if isinstance(v, float):
            if abs(v) >= 10000:
                return f"{v:,.{decimals}f}"
            return f"{v:.{decimals}f}"
        return str(v)


# ==========================================
# 便捷函数
# ==========================================

def analyze_excel(file_path: str, sheet_name: str | None = None) -> AnalysisReport:
    """分析 Excel 文件"""
    engine = AnalysisEngine()
    return engine.analyze_file(file_path, sheet_name)


def analyze_data(data: List[List], headers: List[str] | None = None,
                 sheet_name: str = "Sheet1") -> AnalysisReport:
    """分析数据（二维列表）"""
    engine = AnalysisEngine()
    return engine.analyze_data(data, headers, sheet_name)
