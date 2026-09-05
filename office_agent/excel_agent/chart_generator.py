"""
Chart Generator - Excel 智能图表生成器

支持图表类型：
- 柱状图 column / 条形图 bar（含堆积）
- 折线图 line（含标记点）
- 饼图 pie / 环形图 doughnut
- 面积图 area（含堆积）
- 散点图 scatter / 气泡图 bubble
- 雷达图 radar
- 组合图 combo（柱+线，支持次坐标轴）

智能选图：
- 时间序列 → 折线图
- 分类对比（≤12类）→ 柱状图
- 分类对比（>12类）→ 条形图
- 占比构成 → 饼图/环形图
- 多指标对比+趋势 → 组合图
- 相关性 → 散点图
- 多维对比 → 雷达图
"""
import logging
import re
from typing import Optional, List, Tuple
from openpyxl.chart import (
    BarChart, LineChart, PieChart, AreaChart, ScatterChart,
    DoughnutChart, RadarChart, Reference, Series,
)
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.series import DataPoint
from openpyxl.utils import quote_sheetname

from .models import ChartSpec, DataProfile, SheetInfo, ColumnInfo

logger = logging.getLogger("office_agent.excel_agent.chart_generator")


# ==========================================
# 配色方案
# ==========================================

COLOR_PALETTES = {
    "professional": [
        "1F4E79", "2E75B6", "FFC000", "70AD47",
        "C00000", "7030A0", "00B0F0", "ED7D31",
    ],
    "fresh": [
        "4472C4", "70AD47", "FFC000", "ED7D31",
        "5B9BD5", "A5A5A5", "264478", "9E480E",
    ],
    "warm": [
        "C00000", "ED7D31", "FFC000", "70AD47",
        "4472C4", "7030A0", "00B0F0", "FF5050",
    ],
    "tech": [
        "0066CC", "00B0F0", "00FF88", "FF6600",
        "9933FF", "FF3366", "33CCFF", "FFCC00",
    ],
}

DEFAULT_PALETTE = COLOR_PALETTES["professional"]


# ==========================================
# 图表类型推荐规则
# ==========================================

CHART_TYPE_RULES = {
    "line": {
        "keywords": ["趋势", "变化", "走势", "增长", "时间", "月度", "年度",
                     "季度", "trend", "over time", "line", "折线", "发展"],
        "description": "折线图 - 展示时间序列趋势",
    },
    "column": {
        "keywords": ["对比", "比较", "各", "不同", "compare", "column",
                     "柱状", "柱形", "条形图"],
        "description": "柱状图 - 分类对比",
    },
    "bar": {
        "keywords": ["排名", "排行", "top", "best", "worst", "ranking",
                     "横向", "水平"],
        "description": "条形图 - 排名/多分类",
    },
    "pie": {
        "keywords": ["占比", "比例", "构成", "分布", "份额", "组成",
                     "proportion", "share", "pie", "饼图", "百分比"],
        "description": "饼图 - 占比构成",
    },
    "doughnut": {
        "keywords": ["环形", "甜甜圈", "doughnut", "donut"],
        "description": "环形图 - 占比构成",
    },
    "area": {
        "keywords": ["面积", "区域", "累计", "累积", "area", "堆积面积"],
        "description": "面积图 - 累计/堆积",
    },
    "scatter": {
        "keywords": ["关系", "相关", "关联", "correlation", "vs",
                     "scatter", "散点", "xy"],
        "description": "散点图 - 相关性",
    },
    "combo": {
        "keywords": ["组合", "柱线", "同时", "对比和趋势", "combo",
                     "双轴", "次轴", "柱状和折线"],
        "description": "组合图 - 柱+线双轴",
    },
    "radar": {
        "keywords": ["雷达", "多维", "综合", "能力", "radar", "蜘蛛"],
        "description": "雷达图 - 多维对比",
    },
}


class ChartGenerator:
    """
    Excel 智能图表生成器

    用法:
        gen = ChartGenerator()

        # 1. 从自然语言生成图表并写入 Excel
        gen.generate_to_file("销售数据.xlsx", "展示销售额趋势", "output.xlsx")

        # 2. 自动推荐图表
        specs = gen.auto_charts_from_file("data.xlsx")

        # 3. 手动创建
        spec = gen.create_chart("line", title="趋势", data_range="A1:B13")
    """

    def __init__(self, profile: DataProfile | None = None):
        self.profile = profile
        self.palette = DEFAULT_PALETTE

    def set_profile(self, profile: DataProfile):
        self.profile = profile

    # ==========================================
    # 从文件直接生成图表
    # ==========================================

    def generate_to_file(self, file_path: str, text: str = "",
                         output_path: str | None = None,
                         sheet_name: str | None = None) -> Tuple[str, List[ChartSpec]]:
        """从自然语言生成图表并写入 Excel"""
        from .excel_service import ExcelService, _derive_output_path

        service = ExcelService()
        service.open(file_path)

        if sheet_name is None:
            sheet_name = service.wb.sheetnames[0]

        # 分析数据
        if self.profile is None:
            try:
                from .data_analyzer import DataAnalyzer
                self.profile = DataAnalyzer().analyze(file_path)
            except Exception as exc:
                # 画像失败降级为无 profile 选图：继续生成，但原因必须可观测
                logger.warning(
                    "数据分析画像失败，降级为无画像选图 %s: %s",
                    file_path, exc, exc_info=True,
                )

        # 生成图表规格
        if text:
            specs = self.generate_from_text(text, sheet_name)
        else:
            specs = self.auto_charts(sheet_name)

        # 写入图表
        for spec in specs:
            self._render_chart(service, spec, sheet_name)

        if output_path is None:
            output_path = _derive_output_path(file_path, "_charts")
        service.save(output_path)

        return output_path, specs

    def auto_generate_to_file(self, file_path: str,
                               output_path: str | None = None,
                               sheet_name: str | None = None) -> Tuple[str, List[ChartSpec]]:
        """自动分析数据并生成推荐图表"""
        return self.generate_to_file(file_path, "", output_path, sheet_name)

    # ==========================================
    # 从自然语言生成 ChartSpec
    # ==========================================

    def generate_from_text(self, text: str,
                           sheet_name: str | None = None,
                           chart_type: str = "") -> List[ChartSpec]:
        """从自然语言生成图表规格；chart_type 显式给定时优先"""
        charts: List[ChartSpec] = []
        sheet = self.profile.get_sheet(sheet_name) if self.profile else None

        # 识别图表类型：显式参数优先，否则从文本推断
        chart_type = (chart_type if chart_type in CHART_TYPE_RULES
                      else self._detect_chart_type(text))

        # 识别目标列
        target_cols = self._detect_columns(text, sheet)
        if not target_cols and sheet:
            target_cols = [c for c in sheet.columns if c.data_type == "number"]

        if not target_cols or not sheet or sheet.row_count <= 0:
            return charts

        # 查找类别列
        cat_col = self._find_category_column(sheet)
        cat_letter = self._col_letter(cat_col.index) if cat_col else "A"

        end_row = sheet.row_count + 1  # 包含表头

        if chart_type == "combo":
            # 组合图：第一个系列柱状，第二个系列折线（次轴）
            spec = self._build_combo_spec(
                target_cols, cat_col, sheet, text, position_index=len(charts)
            )
            if spec:
                charts.append(spec)

        elif chart_type == "scatter":
            # 散点图：X=第一个数值列，Y=第二个数值列
            spec = self._build_scatter_spec(
                target_cols, sheet, text, position_index=len(charts)
            )
            if spec:
                charts.append(spec)

        elif chart_type in ("pie", "doughnut"):
            # 饼图：data_range 只含数值列，类别由 categories_range 提供
            # （原实现把类别列也当数据系列，饼图必然画错/为空）
            col = target_cols[0]
            col_letter = self._col_letter(col.index)
            charts.append(ChartSpec(
                chart_type=chart_type,
                title=self._make_title(text, col.name),
                data_range=f"{col_letter}1:{col_letter}{end_row}",
                categories_range=f"{cat_letter}2:{cat_letter}{end_row}",
                position=self._next_chart_position(len(charts)),
                show_data_labels=True,
            ))

        elif chart_type == "radar":
            # 雷达图
            for col in target_cols[:4]:
                col_letter = self._col_letter(col.index)
                charts.append(ChartSpec(
                    chart_type="radar",
                    title=self._make_title(text, col.name),
                    data_range=f"{col_letter}1:{col_letter}{end_row}",
                    categories_range=f"{cat_letter}2:{cat_letter}{end_row}",
                    position=self._next_chart_position(len(charts)),
                ))

        else:
            # 柱状/条形/折线/面积
            for col in target_cols[:4]:
                col_letter = self._col_letter(col.index)
                charts.append(ChartSpec(
                    chart_type=chart_type,
                    title=self._make_title(text, col.name),
                    data_range=f"{col_letter}1:{col_letter}{end_row}",
                    categories_range=f"{cat_letter}2:{cat_letter}{end_row}",
                    x_title=cat_col.name if cat_col else "",
                    y_title=col.name,
                    position=self._next_chart_position(len(charts)),
                    show_data_labels=(chart_type in ("column", "bar") and sheet.row_count <= 8),
                ))

        return charts

    # ==========================================
    # 智能推荐图表
    # ==========================================

    def auto_charts(self, sheet_name: str | None = None) -> List[ChartSpec]:
        """根据数据特征自动推荐图表"""
        charts: List[ChartSpec] = []
        sheet = self.profile.get_sheet(sheet_name) if self.profile else None
        if not sheet or sheet.row_count <= 0:
            return charts

        num_cols = [c for c in sheet.columns if c.data_type == "number"]
        date_cols = [c for c in sheet.columns if c.data_type == "date"]
        cat_col = self._find_category_column(sheet)

        if not num_cols or not cat_col:
            return charts

        cat_letter = self._col_letter(cat_col.index)
        end_row = sheet.row_count + 1
        n_categories = sheet.row_count

        # 判断是否时间序列
        is_time_series = bool(date_cols) or self._is_time_like(cat_col)

        # 1. 时间序列 → 折线图
        if is_time_series:
            for col in num_cols[:2]:
                col_letter = self._col_letter(col.index)
                charts.append(ChartSpec(
                    chart_type="line",
                    title=f"{col.name}趋势",
                    data_range=f"{col_letter}1:{col_letter}{end_row}",
                    categories_range=f"{cat_letter}2:{cat_letter}{end_row}",
                    x_title=cat_col.name,
                    y_title=col.name,
                    position=self._next_chart_position(len(charts)),
                    width=18, height=10,
                ))

            # 如果有2个以上指标且量级差异大 → 组合图
            if len(num_cols) >= 2:
                combo_spec = self._build_combo_spec(
                    num_cols[:2], cat_col, sheet, "", position_index=len(charts)
                )
                if combo_spec:
                    combo_spec.title = f"{num_cols[0].name}与{num_cols[1].name}"
                    charts.append(combo_spec)

        # 2. 分类对比
        if n_categories <= 12:
            # ≤12类 → 柱状图
            for col in num_cols[:2]:
                col_letter = self._col_letter(col.index)
                charts.append(ChartSpec(
                    chart_type="column",
                    title=f"{col.name}对比",
                    data_range=f"{col_letter}1:{col_letter}{end_row}",
                    categories_range=f"{cat_letter}2:{cat_letter}{end_row}",
                    x_title=cat_col.name,
                    y_title=col.name,
                    position=self._next_chart_position(len(charts)),
                    show_data_labels=(n_categories <= 8),
                ))
        else:
            # >12类 → 条形图
            col = num_cols[0]
            col_letter = self._col_letter(col.index)
            charts.append(ChartSpec(
                chart_type="bar",
                title=f"{col.name}排名",
                data_range=f"{col_letter}1:{col_letter}{end_row}",
                categories_range=f"{cat_letter}2:{cat_letter}{end_row}",
                x_title=col.name,
                y_title=cat_col.name,
                position=self._next_chart_position(len(charts)),
                width=15, height=max(10, n_categories * 0.5),
            ))

        # 3. 占比构成 → 饼图（类别少时）
        if n_categories <= 8 and not is_time_series:
            col = num_cols[0]
            col_letter = self._col_letter(col.index)
            charts.append(ChartSpec(
                chart_type="pie",
                title=f"{col.name}占比",
                data_range=f"{col_letter}1:{col_letter}{end_row}",
                categories_range=f"{cat_letter}2:{cat_letter}{end_row}",
                position=self._next_chart_position(len(charts)),
                show_data_labels=True,
            ))

        # 4. 多数值列 → 堆积柱状图（如果有2个以上可加性指标）
        if len(num_cols) >= 2 and not is_time_series and n_categories <= 10:
            first_letter = self._col_letter(num_cols[0].index)
            last_letter = self._col_letter(num_cols[-1].index)
            charts.append(ChartSpec(
                chart_type="column",
                title="各指标构成",
                data_range=f"{first_letter}1:{last_letter}{end_row}",
                categories_range=f"{cat_letter}2:{cat_letter}{end_row}",
                position=self._next_chart_position(len(charts)),
                stacked=True,
            ))

        return charts

    # ==========================================
    # 手动创建图表
    # ==========================================

    def create_chart(self, chart_type: str = "column",
                     title: str = "",
                     data_range: str = "",
                     categories_range: str = "",
                     x_title: str = "",
                     y_title: str = "",
                     position: str = "",
                     **kwargs) -> ChartSpec:
        """直接创建图表规格"""
        return ChartSpec(
            chart_type=chart_type,
            title=title,
            data_range=data_range,
            categories_range=categories_range,
            x_title=x_title,
            y_title=y_title,
            position=position or "H2",
            **kwargs,
        )

    # ==========================================
    # 图表渲染（openpyxl）
    # ==========================================

    def _render_chart(self, service, spec: ChartSpec, sheet_name: str | None = None):
        """将 ChartSpec 渲染到 Excel"""
        ws = service.get_sheet(sheet_name)
        if ws is None or not spec.data_range or not self._range_has_data_rows(spec.data_range):
            return

        chart_count = len(ws._charts)
        if spec.chart_type == "combo":
            self._render_combo(ws, spec)
        elif spec.chart_type == "scatter":
            self._render_scatter(ws, spec)
        else:
            self._render_standard(ws, spec)

        if len(ws._charts) > chart_count:
            service.changes.append(f"添加图表: {spec.title or spec.chart_type}")

    @staticmethod
    def _range_has_data_rows(range_str: str) -> bool:
        """图表数据范围约定首行为标题，至少还需一行真实数据。"""
        from openpyxl.utils import range_boundaries
        try:
            _min_col, min_row, _max_col, max_row = range_boundaries(range_str)
        except (TypeError, ValueError):
            return False
        return max_row > min_row

    def _render_standard(self, ws, spec: ChartSpec):
        """渲染标准图表（柱/条/线/饼/面积/雷达/环形）"""
        chart_classes = {
            "column": BarChart,
            "bar": BarChart,
            "line": LineChart,
            "pie": PieChart,
            "doughnut": DoughnutChart,
            "area": AreaChart,
            "radar": RadarChart,
        }
        chart_cls = chart_classes.get(spec.chart_type, BarChart)
        chart = chart_cls()

        # 类型设置
        if spec.chart_type == "bar":
            chart.type = "bar"
        elif spec.chart_type == "column":
            chart.type = "col"

        # 堆积
        if spec.stacked:
            chart.grouping = "stacked"
            chart.overlap = 100

        # 折线图带标记
        if spec.chart_type == "line":
            chart.marker = True

        # 基本属性
        chart.title = spec.title or ""
        chart.style = spec.style or 10

        # 数据
        sheet_ref = quote_sheetname(ws.title)
        if spec.series_ranges:
            from openpyxl.chart.series import SeriesLabel
            for index, range_str in enumerate(spec.series_ranges):
                data = Reference(ws, range_string=f"{sheet_ref}!{range_str}")
                chart.add_data(data, titles_from_data=True)
                if index < len(spec.series_names) and chart.series:
                    chart.series[-1].tx = SeriesLabel(v=str(spec.series_names[index]))
        else:
            data = Reference(ws, range_string=f"{sheet_ref}!{spec.data_range}")
            chart.add_data(data, titles_from_data=True)

        # 类别
        if spec.categories_range:
            cats = Reference(ws, range_string=f"{sheet_ref}!{spec.categories_range}")
            chart.set_categories(cats)

        # 轴标题
        if spec.y_title and spec.chart_type not in ("pie", "doughnut", "radar"):
            chart.y_axis.title = spec.y_title
        if spec.x_title and spec.chart_type not in ("pie", "doughnut", "radar"):
            chart.x_axis.title = spec.x_title

        # 数据标签
        if spec.show_data_labels:
            chart.dataLabels = DataLabelList()
            if spec.chart_type in ("pie", "doughnut"):
                chart.dataLabels.showPercent = True
                chart.dataLabels.showCatName = True
            else:
                chart.dataLabels.showVal = True

        # 图例
        if not spec.show_legend:
            chart.legend = None
        else:
            from openpyxl.chart.legend import Legend
            chart.legend = Legend()
            pos_map = {"bottom": "b", "top": "t", "right": "r", "left": "l"}
            chart.legend.position = pos_map.get(spec.legend_position, "b")

        # 颜色
        from openpyxl.utils import range_boundaries
        _min_col, min_row, _max_col, max_row = range_boundaries(spec.data_range)
        point_count = max(0, max_row - min_row)
        self._apply_colors(
            chart, spec.color_palette or self.palette,
            chart_type=spec.chart_type, point_count=point_count,
        )

        # 大小（openpyxl ChartBase.width/height 单位即为厘米）
        chart.width = spec.width
        chart.height = spec.height

        # 添加
        anchor = spec.position or "H2"
        ws.add_chart(chart, anchor)

    def _render_combo(self, ws, spec: ChartSpec):
        """渲染组合图（柱+线，支持次轴）

        引用建模：优先消费 ``spec.series_ranges``（每系列真实列引用）
        与 ``spec.categories_range``（显式类别列引用）——类别列可以在
        任意位置、数据列可以不连续。无 series_ranges 的旧 ChartSpec
        回退到连续矩形拆分（第一列类别、其余连续数据），行为不变。
        """

        from openpyxl.utils import range_boundaries

        if spec.series_ranges:
            # 显式引用路径：类别列与每个系列各自定位
            sheet_ref = quote_sheetname(ws.title)
            cat_min_col, cat_min_row, cat_max_col, cat_max_row = range_boundaries(
                spec.categories_range
            )
            cats = Reference(ws, min_col=cat_min_col, min_row=cat_min_row,
                             max_col=cat_max_col, max_row=cat_max_row)
            series_refs = [
                Reference(ws, range_string=f"{sheet_ref}!{rng}")
                for rng in spec.series_ranges
            ]
        else:
            # 兼容路径：data_range 格式 A1:D13（类别列+连续数据列）
            parts = spec.data_range.split(":")
            if len(parts) != 2:
                return

            min_col, min_row, max_col, max_row = range_boundaries(spec.data_range)

            # 类别列
            cats = Reference(ws, min_col=min_col, min_row=min_row + 1,
                            max_col=min_col, max_row=max_row)
            series_refs = [
                Reference(ws, min_col=min_col + 1, min_row=min_row,
                          max_col=min_col + 1, max_row=max_row),
            ]
            if max_col > min_col + 1:
                series_refs.append(
                    Reference(ws, min_col=min_col + 2, min_row=min_row,
                              max_col=max_col, max_row=max_row)
                )

        # 创建柱状图（第一个系列）
        bar_chart = BarChart()
        bar_chart.type = "col"
        bar_chart.title = spec.title or ""
        bar_chart.style = spec.style or 10

        bar_chart.add_data(series_refs[0], titles_from_data=True)
        bar_chart.set_categories(cats)
        if spec.y_title:
            bar_chart.y_axis.title = spec.y_title

        # 创建折线图（其余系列，次轴）；没有第二个系列时不合并空图
        if len(series_refs) > 1:
            line_chart = LineChart()
            for ref in series_refs[1:]:
                line_chart.add_data(ref, titles_from_data=True)
            line_chart.y_axis.axId = 200
            # openpyxl 次轴配方：crosses 设置在主图 y 轴上
            bar_chart.y_axis.crosses = "max"
            # 组合
            bar_chart += line_chart

        # 颜色
        self._apply_colors(bar_chart, spec.color_palette or self.palette)

        # 大小（厘米）
        bar_chart.width = spec.width
        bar_chart.height = spec.height

        anchor = spec.position or "H2"
        ws.add_chart(bar_chart, anchor)

    def _render_scatter(self, ws, spec: ChartSpec):
        """渲染散点图"""
        chart = ScatterChart()
        chart.title = spec.title or ""
        chart.style = spec.style or 10

        from openpyxl.utils import range_boundaries
        if spec.x_values_range and spec.y_values_range:
            try:
                x_bounds = range_boundaries(spec.x_values_range)
                y_bounds = range_boundaries(spec.y_values_range)
            except (TypeError, ValueError):
                return
            if (x_bounds[3] - x_bounds[1]) != (y_bounds[3] - y_bounds[1]):
                return
            sheet_ref = quote_sheetname(ws.title)
            xvalues = Reference(
                ws, range_string=f"{sheet_ref}!{spec.x_values_range}"
            )
            yvalues = Reference(
                ws, range_string=f"{sheet_ref}!{spec.y_values_range}"
            )
        else:
            # 兼容旧 ChartSpec：连续两列仍按第一列 X、第二列 Y 解释。
            min_col, min_row, max_col, max_row = range_boundaries(spec.data_range)
            if max_col < min_col + 1:
                return
            xvalues = Reference(ws, min_col=min_col, min_row=min_row + 1,
                               max_row=max_row)
            yvalues = Reference(ws, min_col=min_col + 1, min_row=min_row + 1,
                               max_row=max_row)
        series = Series(yvalues, xvalues, title=spec.y_title or "Y")
        chart.series.append(series)

        if spec.x_title:
            chart.x_axis.title = spec.x_title
        if spec.y_title:
            chart.y_axis.title = spec.y_title

        chart.width = spec.width
        chart.height = spec.height

        anchor = spec.position or "H2"
        ws.add_chart(chart, anchor)

    def _apply_colors(self, chart, palette: List[str],
                      chart_type: str = "", point_count: int = 0):
        """应用配色"""
        from openpyxl.chart.shapes import GraphicalProperties
        from openpyxl.drawing.line import LineProperties

        for i, series in enumerate(chart.series):
            color = palette[i % len(palette)]
            # 填充色
            series.graphicalProperties = GraphicalProperties()
            series.graphicalProperties.solidFill = color
            # 线条色
            series.graphicalProperties.line = LineProperties(solidFill=color)

        # 饼图只有一个 series，必须按数据点着色，否则整张饼图是同一种颜色。
        if chart_type in ("pie", "doughnut") and chart.series:
            chart.series[0].data_points = [
                DataPoint(
                    idx=index,
                    spPr=GraphicalProperties(solidFill=palette[index % len(palette)]),
                )
                for index in range(point_count)
            ]

    # ==========================================
    # 构建特定图表规格
    # ==========================================

    def _build_combo_spec(self, target_cols: List[ColumnInfo],
                           cat_col: ColumnInfo, sheet: SheetInfo,
                           text: str, position_index: int = 0) -> Optional[ChartSpec]:
        """构建组合图规格"""
        if len(target_cols) < 2 or sheet.row_count <= 0:
            return None

        cat_letter = self._col_letter(cat_col.index)
        last_letter = self._col_letter(target_cols[1].index)
        end_row = sheet.row_count + 1

        title = self._make_title(text, "")
        if not title:
            title = f"{target_cols[0].name}与{target_cols[1].name}"

        return ChartSpec(
            chart_type="combo",
            title=title,
            data_range=f"{cat_letter}1:{last_letter}{end_row}",
            categories_range=f"{cat_letter}2:{cat_letter}{end_row}",
            # 每个系列使用自己的真实列引用（含表头行）：类别列可以在
            # 任意位置、数据列可以不连续，渲染侧优先消费本字段；
            # data_range 的连续矩形仅为兼容保留。
            series_ranges=[
                f"{self._col_letter(col.index)}1:{self._col_letter(col.index)}{end_row}"
                for col in target_cols[:2]
            ],
            x_title=cat_col.name,
            y_title=target_cols[0].name,
            position=self._next_chart_position(position_index),
            combo_types=["column", "line"],
            combo_secondary=[False, True],
            width=18, height=10,
        )

    def _build_scatter_spec(self, target_cols: List[ColumnInfo],
                             sheet: SheetInfo, text: str,
                             position_index: int = 0) -> Optional[ChartSpec]:
        """构建散点图规格"""
        if len(target_cols) < 2 or sheet.row_count <= 0:
            return None

        first_letter = self._col_letter(target_cols[0].index)
        last_letter = self._col_letter(target_cols[1].index)
        end_row = sheet.row_count + 1

        return ChartSpec(
            chart_type="scatter",
            title=self._make_title(text, f"{target_cols[0].name} vs {target_cols[1].name}"),
            data_range=f"{first_letter}1:{last_letter}{end_row}",
            x_values_range=f"{first_letter}2:{first_letter}{end_row}",
            y_values_range=f"{last_letter}2:{last_letter}{end_row}",
            x_title=target_cols[0].name,
            y_title=target_cols[1].name,
            position=self._next_chart_position(position_index),
            width=15, height=12,
        )

    # ==========================================
    # 识别辅助方法
    # ==========================================

    def _detect_chart_type(self, text: str) -> str:
        """从文本识别图表类型（长关键词优先匹配）"""
        text_lower = text.lower()
        # 收集所有匹配项，按关键词长度降序排列（更具体的优先）
        matches = []
        for chart_type, rule in CHART_TYPE_RULES.items():
            for kw in rule["keywords"]:
                if kw.lower() in text_lower:
                    matches.append((len(kw), chart_type))
        if matches:
            matches.sort(key=lambda x: -x[0])
            return matches[0][1]
        return "column"

    def _detect_columns(self, text: str, sheet: SheetInfo | None = None) -> List[ColumnInfo]:
        """识别目标列"""
        if not sheet:
            return []
        matched = []
        for col in sheet.columns:
            if col.name and col.name in text:
                matched.append(col)
        return matched

    def _find_category_column(self, sheet: SheetInfo) -> Optional[ColumnInfo]:
        """查找类别列（优先日期/时间，其次文本）"""
        # 优先日期列
        for col in sheet.columns:
            if col.data_type == "date":
                return col
        # 其次时间类文本列
        time_keywords = ["月", "年", "季度", "周", "日期", "时间", "期"]
        for col in sheet.columns:
            if col.data_type == "text":
                for kw in time_keywords:
                    if kw in str(col.name):
                        return col
        # 第一个文本列
        for col in sheet.columns:
            if col.data_type == "text":
                return col
        return None

    def _is_time_like(self, col: ColumnInfo | None = None) -> bool:
        """判断列是否像时间序列"""
        if col is None:
            return False
        if col.data_type == "date":
            return True
        name = str(col.name).lower()
        time_kw = ["月", "年", "季度", "周", "日期", "时间", "month", "year", "quarter", "date"]
        return any(kw in name for kw in time_kw)

    @staticmethod
    def _col_letter(index: int) -> str:
        """0-based 列索引转 Excel 列字母（统一入口在 models.col_letter）"""
        from .models import col_letter
        return col_letter(index)

    @staticmethod
    def _next_chart_position(index: int) -> str:
        """计算下一个图表位置（避免重叠）"""
        row = 2 + (index // 2) * 16
        col = "H" if index % 2 == 0 else "R"
        return f"{col}{row}"

    @staticmethod
    def _make_title(text: str, col_name: str = "") -> str:
        """生成图表标题"""
        clean = re.sub(r"(画|生成|做|创建|绘制|展示|显示|一张|一个|图表|图|给我|帮我)", "", text)
        clean = clean.strip()
        if clean and col_name and col_name not in clean:
            return f"{clean} - {col_name}"
        return clean or col_name or "数据图表"


# ==========================================
# 便捷函数
# ==========================================

def generate_charts(text: str, profile: DataProfile | None = None,
                    sheet_name: str | None = None) -> List[ChartSpec]:
    """从自然语言生成图表规格"""
    gen = ChartGenerator(profile)
    return gen.generate_from_text(text, sheet_name)


def auto_charts_from_file(file_path: str,
                          sheet_name: str | None = None) -> List[ChartSpec]:
    """从文件自动推荐图表"""
    from .data_analyzer import DataAnalyzer
    gen = ChartGenerator()
    gen.set_profile(DataAnalyzer().analyze(file_path))
    return gen.auto_charts(sheet_name)


def generate_charts_to_file(file_path: str, text: str = "",
                            output_path: str | None = None,
                            sheet_name: str | None = None) -> Tuple[str, List[ChartSpec]]:
    """生成图表并写入 Excel"""
    gen = ChartGenerator()
    return gen.generate_to_file(file_path, text, output_path, sheet_name)
