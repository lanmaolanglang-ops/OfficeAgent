"""
Excel Template Analyzer - Excel 模板分析器

分析已有 Excel 模板的完整样式和结构：
- Sheet 结构（行列数、表头位置、数据起始行）
- 列宽、行高
- 字体（名称、大小、粗体、斜体、颜色）
- 填充色、边框、对齐
- 数字格式
- 公式（支持 {row} 占位符模板）
- 合并单元格
- 冻结窗格
- 自动筛选
- 条件格式
- 数据验证（下拉列表等）

然后将新数据按模板格式自动填充。
"""
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .models import FormatSpec


# ==========================================
# 数据模型
# ==========================================

@dataclass
class BorderStyleInfo:
    """边框样式"""
    left_style: str = ""       # thin, medium, thick, double, dashed, dotted
    left_color: str = ""
    right_style: str = ""
    right_color: str = ""
    top_style: str = ""
    top_color: str = ""
    bottom_style: str = ""
    bottom_color: str = ""

    def has_border(self) -> bool:
        return any([self.left_style, self.right_style, self.top_style, self.bottom_style])


@dataclass
class CellStyleInfo:
    """单元格样式信息"""
    font_name: str = "微软雅黑"
    font_size: int = 10
    bold: bool = False
    italic: bool = False
    font_color: str = "000000"
    bg_color: str = ""
    number_format: str = "General"
    alignment: str = "left"       # left/center/right
    vertical_alignment: str = "center"  # top/center/bottom
    wrap_text: bool = False
    border: BorderStyleInfo = field(default_factory=BorderStyleInfo)

    def to_format_spec(self, range_str: str) -> FormatSpec:
        return FormatSpec(
            range_str=range_str,
            font_name=self.font_name,
            font_size=self.font_size,
            bold=self.bold,
            italic=self.italic,
            font_color=self.font_color,
            bg_color=self.bg_color,
            number_format=self.number_format,
            alignment=self.alignment,
            border=self.border.has_border(),
        )


@dataclass
class ConditionalFormatInfo:
    """条件格式"""
    range_str: str = ""
    rule_type: str = ""          # colorScale, dataBar, cellIs, expression
    operator: str = ""
    formula: str = ""
    colors: List[str] = field(default_factory=list)  # 色阶颜色
    min_value: str = ""
    max_value: str = ""


@dataclass
class DataValidationInfo:
    """数据验证"""
    range_str: str = ""
    type: str = ""               # list, whole, decimal, date, textLength
    formula1: str = ""           # 下拉列表来源
    allow_blank: bool = True
    show_dropdown: bool = True


@dataclass
class MergedCellInfo:
    """合并单元格"""
    range_str: str = ""
    value: Any = None
    style: Optional[CellStyleInfo] = None


@dataclass
class ColumnTemplate:
    """列模板"""
    col_index: int              # 0-based
    col_letter: str
    header: str
    header_style: Optional[CellStyleInfo] = None
    body_style: Optional[CellStyleInfo] = None
    width: float = 12.0
    has_formula: bool = False
    formula_pattern: str = ""   # 公式模板，如 "=SUM(B{row}:C{row})"
    number_format: str = "General"
    hidden: bool = False


@dataclass
class RowTemplate:
    """行模板（表头行/标题行等特殊行）"""
    row_number: int             # 1-based
    height: float = 15.0
    style: Optional[CellStyleInfo] = None
    is_header: bool = False
    is_title: bool = False


@dataclass
class SheetTemplate:
    """工作表模板"""
    name: str
    row_count: int = 0
    col_count: int = 0
    columns: List[ColumnTemplate] = field(default_factory=list)
    rows: List[RowTemplate] = field(default_factory=list)
    merged_cells: List[MergedCellInfo] = field(default_factory=list)
    conditional_formats: List[ConditionalFormatInfo] = field(default_factory=list)
    data_validations: List[DataValidationInfo] = field(default_factory=list)

    # 结构
    header_row: int = 1
    data_start_row: int = 2
    title_row: int = 0          # 标题行（如果有）

    # 特性
    has_header_style: bool = True
    has_borders: bool = False
    has_zebra: bool = False
    zebra_color: str = "F5F7FA"
    freeze_panes: str = ""      # 如 "A2", "B2"
    auto_filter: str = ""       # 如 "A1:D10"
    tab_color: str = ""

    # 打印设置
    print_area: str = ""
    page_orientation: str = ""  # landscape/portrait


@dataclass
class ExcelTemplateConfig:
    """Excel 模板配置"""
    file_path: str = ""
    name: str = ""
    sheets: List[SheetTemplate] = field(default_factory=list)

    def get_sheet(self, name: str | None = None) -> Optional[SheetTemplate]:
        if name is None:
            return self.sheets[0] if self.sheets else None
        for s in self.sheets:
            if s.name == name:
                return s
        return None

    def to_dict(self) -> dict:
        """转为字典（用于序列化）"""
        return {
            "name": self.name,
            "file_path": self.file_path,
            "sheets": [
                {
                    "name": s.name,
                    "row_count": s.row_count,
                    "col_count": s.col_count,
                    "header_row": s.header_row,
                    "data_start_row": s.data_start_row,
                    "freeze_panes": s.freeze_panes,
                    "auto_filter": s.auto_filter,
                    "columns": [
                        {
                            "header": c.header,
                            "col_letter": c.col_letter,
                            "width": c.width,
                            "number_format": c.number_format,
                            "has_formula": c.has_formula,
                            "formula_pattern": c.formula_pattern,
                        }
                        for c in s.columns
                    ],
                }
                for s in self.sheets
            ],
        }


# ==========================================
# 分析器
# ==========================================

class ExcelTemplateAnalyzer:
    """
    Excel 模板分析器

    用法:
        analyzer = ExcelTemplateAnalyzer()

        # 1. 分析模板
        config = analyzer.analyze("template.xlsx")

        # 2. 用模板填充新数据
        analyzer.fill_template("template.xlsx", new_data, "output.xlsx")

        # 3. 或获取格式规格
        specs = analyzer.get_format_specs(config)
    """

    def analyze(self, file_path: str) -> ExcelTemplateConfig:
        """分析 Excel 模板"""
        if not Path(file_path).exists():
            raise FileNotFoundError(f"模板文件不存在: {file_path}")

        wb = load_workbook(file_path)
        config = ExcelTemplateConfig(
            file_path=file_path,
            name=Path(file_path).stem,
        )

        for ws in wb.worksheets:
            sheet_tpl = self._analyze_sheet(ws)
            config.sheets.append(sheet_tpl)

        wb.close()
        return config

    # ==========================================
    # 分析单个 Sheet
    # ==========================================

    def _analyze_sheet(self, ws) -> SheetTemplate:
        """分析单个工作表"""
        tpl = SheetTemplate(
            name=ws.title,
            row_count=ws.max_row or 1,
            col_count=ws.max_column or 1,
        )

        # 标签颜色
        if ws.sheet_properties.tabColor:
            tpl.tab_color = str(ws.sheet_properties.tabColor.rgb or "")

        # 检测表头行和数据起始行
        tpl.header_row, tpl.data_start_row = self._detect_header_row(ws)

        # 分析列
        for col_idx in range(1, (ws.max_column or 1) + 1):
            col_tpl = self._analyze_column(ws, col_idx, tpl.header_row, tpl.data_start_row)
            tpl.columns.append(col_tpl)

        # 分析行高（前20行）
        for row_idx in range(1, min(21, (ws.max_row or 1) + 1)):
            row_dim = ws.row_dimensions.get(row_idx)
            if row_dim and row_dim.height:
                rt = RowTemplate(
                    row_number=row_idx,
                    height=row_dim.height,
                    is_header=(row_idx == tpl.header_row),
                )
                # 取该行第一个非空单元格样式
                for c in range(1, (ws.max_column or 1) + 1):
                    cell = ws.cell(row=row_idx, column=c)
                    if cell.value is not None:
                        rt.style = self._extract_style(cell)
                        break
                tpl.rows.append(rt)

        # 合并单元格
        for merged_range in ws.merged_cells.ranges:
            mc = MergedCellInfo(
                range_str=str(merged_range),
            )
            # 获取左上角单元格的值和样式
            top_left = ws.cell(row=merged_range.min_row, column=merged_range.min_col)
            mc.value = top_left.value
            mc.style = self._extract_style(top_left)
            tpl.merged_cells.append(mc)

        # 冻结窗格
        if ws.freeze_panes:
            tpl.freeze_panes = ws.freeze_panes

        # 自动筛选
        if ws.auto_filter and ws.auto_filter.ref:
            tpl.auto_filter = ws.auto_filter.ref

        # 条件格式
        tpl.conditional_formats = self._extract_conditional_formats(ws)

        # 数据验证
        tpl.data_validations = self._extract_data_validations(ws)

        # 边框检测
        tpl.has_borders = self._detect_borders(ws)

        # 斑马纹检测
        tpl.has_zebra, tpl.zebra_color = self._detect_zebra(ws, tpl.data_start_row)

        # 打印设置
        if ws.print_area:
            tpl.print_area = ws.print_area
        if ws.page_setup.orientation:
            tpl.page_orientation = ws.page_setup.orientation

        return tpl

    def _analyze_column(self, ws, col_idx: int,
                         header_row: int, data_start_row: int) -> ColumnTemplate:
        """分析单列"""
        col_letter = get_column_letter(col_idx)

        # 表头
        header_cell = ws.cell(row=header_row, column=col_idx)
        header_val = str(header_cell.value) if header_cell.value else f"列{col_idx}"

        col_tpl = ColumnTemplate(
            col_index=col_idx - 1,
            col_letter=col_letter,
            header=header_val,
            header_style=self._extract_style(header_cell),
        )

        # 数据行样式（取 data_start_row 作为样本）
        if ws.max_row and ws.max_row >= data_start_row:
            body_cell = ws.cell(row=data_start_row, column=col_idx)
            col_tpl.body_style = self._extract_style(body_cell)
            col_tpl.number_format = body_cell.number_format or "General"

            # 检测公式 → 转换为模板（行号替换为 {row}）
            if body_cell.value and isinstance(body_cell.value, str) and body_cell.value.startswith("="):
                col_tpl.has_formula = True
                col_tpl.formula_pattern = self._formula_to_template(
                    body_cell.value, data_start_row
                )

        # 列宽
        col_dim = ws.column_dimensions.get(col_letter)
        if col_dim and col_dim.width:
            col_tpl.width = col_dim.width

        # 隐藏列
        if col_dim and col_dim.hidden:
            col_tpl.hidden = True

        return col_tpl

    def _detect_header_row(self, ws) -> Tuple[int, int]:
        """检测表头行和数据起始行"""
        if not ws.max_row or ws.max_row < 1:
            return 1, 2

        # 找到第一个非空行
        first_non_empty = 1
        for r in range(1, min(5, (ws.max_row or 1) + 1)):
            has_value = False
            for c in range(1, min(5, (ws.max_column or 1) + 1)):
                if ws.cell(row=r, column=c).value is not None:
                    has_value = True
                    break
            if has_value:
                first_non_empty = r
                break

        # 检查第一个非空行是否是标题（合并单元格跨越多列）
        for mc in ws.merged_cells.ranges:
            if mc.min_row == first_non_empty and mc.max_row == first_non_empty and mc.max_col - mc.min_col >= 2:
                # 这是标题行，表头在下一行
                header_row = first_non_empty + 1
                return header_row, header_row + 1

        # 检查第一个非空行是否像表头（加粗/不同填充）
        first_style = self._extract_style(ws.cell(row=first_non_empty, column=1))
        if first_style.bold or first_style.bg_color:
            return first_non_empty, first_non_empty + 1

        # 如果第一个非空行的下一行有不同样式（比如有公式/数字格式），那第一行是表头
        if ws.max_row and ws.max_row > first_non_empty:
            first_val = ws.cell(row=first_non_empty, column=1).value
            next_val = ws.cell(row=first_non_empty + 1, column=1).value
            # 第一行是文本，第二行是数字/公式 → 第一行是表头
            if isinstance(first_val, str) and not isinstance(next_val, str):
                return first_non_empty, first_non_empty + 1

        return first_non_empty, first_non_empty + 1

    # ==========================================
    # 样式提取
    # ==========================================

    def _extract_style(self, cell) -> CellStyleInfo:
        """提取单元格完整样式"""
        style = CellStyleInfo()

        # 字体
        if cell.font:
            style.font_name = cell.font.name or "微软雅黑"
            style.font_size = int(cell.font.size) if cell.font.size else 10
            style.bold = bool(cell.font.bold)
            style.italic = bool(cell.font.italic)
            if cell.font.color:
                try:
                    rgb = cell.font.color.rgb
                    if rgb and isinstance(rgb, str) and len(rgb) >= 6:
                        style.font_color = rgb[-6:]  # 去掉 alpha 通道
                except Exception:
                    pass

        # 填充
        if cell.fill and cell.fill.fgColor:
            try:
                rgb = cell.fill.fgColor.rgb
                if rgb and isinstance(rgb, str) and len(rgb) >= 6:
                    hex_color = rgb[-6:]
                    if hex_color.upper() != "FFFFFF" and hex_color != "00000000":
                        style.bg_color = hex_color
            except Exception:
                pass

        # 对齐
        if cell.alignment:
            style.alignment = cell.alignment.horizontal or "left"
            style.vertical_alignment = cell.alignment.vertical or "center"
            style.wrap_text = bool(cell.alignment.wrap_text)

        # 边框
        style.border = self._extract_border(cell)

        # 数字格式
        style.number_format = cell.number_format or "General"

        return style

    def _extract_border(self, cell) -> BorderStyleInfo:
        """提取边框样式"""
        b = BorderStyleInfo()
        if not cell.border:
            return b

        for side_name in ["left", "right", "top", "bottom"]:
            side = getattr(cell.border, side_name, None)
            if side and side.style:
                setattr(b, f"{side_name}_style", side.style)
                if side.color and side.color.rgb:
                    try:
                        rgb = str(side.color.rgb)
                        if len(rgb) >= 6:
                            setattr(b, f"{side_name}_color", rgb[-6:])
                    except Exception:
                        pass
        return b

    def _extract_conditional_formats(self, ws) -> List[ConditionalFormatInfo]:
        """提取条件格式"""
        cf_list = []
        try:
            for cf_range, rules in ws.conditional_formatting._cf_rules.items():
                for rule in rules:
                    cf = ConditionalFormatInfo(
                        range_str=str(cf_range),
                        rule_type=rule.type or "",
                    )
                    if rule.operator:
                        cf.operator = rule.operator
                    if rule.formula:
                        cf.formula = str(rule.formula[0]) if rule.formula else ""

                    # 色阶
                    if rule.colorScale:
                        cs = rule.colorScale
                        if cs.color:
                            cf.colors = [str(c.rgb or "")[-6:] for c in cs.color if c.rgb]

                    # 数据条
                    if rule.dataBar and rule.dataBar.color:
                        try:
                            cf.colors = [str(rule.dataBar.color.rgb or "")[-6:]]
                        except Exception:
                            pass

                    cf_list.append(cf)
        except Exception:
            pass
        return cf_list

    def _extract_data_validations(self, ws) -> List[DataValidationInfo]:
        """提取数据验证"""
        dv_list = []
        try:
            for dv in ws.data_validations.dataValidation:
                info = DataValidationInfo(
                    type=dv.type or "",
                    formula1=dv.formula1 or "",
                    allow_blank=dv.allowBlank if dv.allowBlank is not None else True,
                )
                # 范围
                ranges = []
                for r in dv.sqref.ranges:
                    ranges.append(str(r))
                info.range_str = ",".join(ranges)
                dv_list.append(info)
        except Exception:
            pass
        return dv_list

    def _detect_borders(self, ws) -> bool:
        """检测是否有边框"""
        max_check = min(10, ws.max_row or 1)
        for row in ws.iter_rows(min_row=1, max_row=max_check, max_col=min(10, ws.max_column or 1)):
            for cell in row:
                b = self._extract_border(cell)
                if b.has_border():
                    return True
        return False

    def _detect_zebra(self, ws, data_start_row: int | None = None) -> Tuple[bool, str]:
        """检测斑马纹（交替行背景色）"""
        bg_colors = []
        max_check = min(20, ws.max_row or 1)
        start = data_start_row or 2

        for row_idx in range(start, max_check + 1):
            cell = ws.cell(row=row_idx, column=1)
            color = ""
            if cell.fill and cell.fill.fgColor:
                try:
                    rgb = cell.fill.fgColor.rgb
                    if rgb and isinstance(rgb, str) and len(rgb) >= 6:
                        hex_color = rgb[-6:]
                        if hex_color.upper() not in ("FFFFFF", "00000000"):
                            color = hex_color
                except Exception:
                    pass
            bg_colors.append(color)

        # 检测交替模式
        for i in range(len(bg_colors) - 1):
            c1, c2 = bg_colors[i], bg_colors[i + 1]
            if c1 != c2 and (c1 == "" or c2 == ""):
                zebra = c1 if c1 else c2
                return True, zebra
            if c1 and c2 and c1 != c2:
                return True, c2

        return False, "F5F7FA"

    # ==========================================
    # 公式模板
    # ==========================================

    @staticmethod
    def _formula_to_template(formula: str, row_num: int) -> str:
        """
        将具体公式转为模板，行号替换为 {row}
        例：=SUM(B2:C2) → =SUM(B{row}:C{row})
            =B2*1.1 → =B{row}*1.1
            =SUM(B$2:B2) → =SUM(B$2:B{row})  (绝对引用保留)
        """
        # 替换非绝对引用的行号（前面没有$）
        # 匹配：字母+数字（数字前不是$）
        def replace_row(match):
            col = match.group(1)
            dollar = match.group(2) or ""
            row = match.group(3)
            if dollar == "$":
                return f"{col}${row}"
            return f"{col}{{{row_num}}}" if False else f"{col}{{row}}"

        # 简单替换：把所有 row_num 替换为 {row}（非绝对引用）
        result = formula
        # 替换 A1 形式中的行号（但保留 $A$1 的绝对行号）
        pattern = r'(\$?[A-Z]+)(\$?)(' + str(row_num) + r')(?![0-9])'
        result = re.sub(pattern, lambda m: m.group(1) + (m.group(2) if m.group(2) else '') + ('{row}' if not m.group(2) else m.group(3)), result)
        return result

    @staticmethod
    def _template_to_formula(template: str, row_num: int) -> str:
        """将公式模板中的 {row} 替换为实际行号"""
        return template.replace("{row}", str(row_num))

    # ==========================================
    # 生成 FormatSpec
    # ==========================================

    def get_format_specs(self, config: ExcelTemplateConfig,
                         sheet_name: str | None = None,
                         data_rows: int | None = None) -> List[FormatSpec]:
        """从模板配置生成 FormatSpec 列表"""
        specs: List[FormatSpec] = []
        sheet = config.get_sheet(sheet_name)
        if not sheet:
            return specs

        end_row = data_rows + sheet.data_start_row - 1 if data_rows else sheet.row_count

        # 表头格式
        if sheet.columns and sheet.columns[0].header_style:
            header_range = f"A{sheet.header_row}:{sheet.columns[-1].col_letter}{sheet.header_row}"
            specs.append(sheet.columns[0].header_style.to_format_spec(header_range))

        # 各列数据格式
        for col in sheet.columns:
            if col.body_style:
                data_range = f"{col.col_letter}{sheet.data_start_row}:{col.col_letter}{end_row}"
                spec = col.body_style.to_format_spec(data_range)
                if col.number_format != "General":
                    spec.number_format = col.number_format
                specs.append(spec)

        return specs

    # ==========================================
    # 模板填充（核心功能）
    # ==========================================

    def fill_template(self, template_path: str,
                      data: List[List],
                      output_path: str,
                      sheet_name: str | None = None,
                      headers: List[str] | None = None,
                      keep_template_data: bool = False) -> str:
        """
        用新数据填充模板

        Args:
            template_path: 模板文件路径
            data: 新数据（二维列表，不含表头）
            output_path: 输出路径
            sheet_name: 工作表名
            headers: 自定义表头（不传则使用模板表头）
            keep_template_data: 是否保留模板中原有数据

        Returns:
            输出文件路径
        """
        config = self.analyze(template_path)
        sheet_tpl = config.get_sheet(sheet_name)
        if not sheet_tpl:
            raise ValueError(f"未找到工作表: {sheet_name}")

        # 加载模板
        wb = load_workbook(template_path)
        ws = wb[sheet_tpl.name]

        # 清除模板数据行（保留表头）
        if not keep_template_data:
            self._clear_data_rows(ws, sheet_tpl)

        # 写入新数据
        start_row = sheet_tpl.data_start_row
        for i, row_data in enumerate(data):
            row_num = start_row + i
            for j, col in enumerate(sheet_tpl.columns):
                cell = ws.cell(row=row_num, column=j + 1)
                if isinstance(cell, MergedCell):
                    continue  # 合并从属单元格只读

                # 公式列：使用公式模板（不管输入数据有没有这一列）
                if col.has_formula and col.formula_pattern:
                    cell.value = self._template_to_formula(col.formula_pattern, row_num)
                elif j < len(row_data):
                    cell.value = row_data[j]
                else:
                    continue

                # 应用数据行样式
                if col.body_style:
                    self._apply_style(cell, col.body_style)

                # 数字格式
                if col.number_format and col.number_format != "General":
                    cell.number_format = col.number_format

        # 设置列宽
        for col in sheet_tpl.columns:
            ws.column_dimensions[col.col_letter].width = col.width

        # 设置行高
        for row_tpl in sheet_tpl.rows:
            if row_tpl.height:
                ws.row_dimensions[row_tpl.row_number].height = row_tpl.height

        # 冻结窗格
        if sheet_tpl.freeze_panes:
            ws.freeze_panes = sheet_tpl.freeze_panes

        # 自动筛选（扩展到新数据范围）
        if sheet_tpl.auto_filter:
            last_col = sheet_tpl.columns[-1].col_letter if sheet_tpl.columns else "A"
            filter_range = f"A{sheet_tpl.header_row}:{last_col}{start_row + len(data) - 1}"
            ws.auto_filter.ref = filter_range

        # 斑马纹
        if sheet_tpl.has_zebra and data:
            self._apply_zebra(ws, sheet_tpl, len(data))

        # 保存
        from pathlib import Path
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        wb.save(output_path)
        wb.close()

        return output_path

    def fill_with_dict(self, template_path: str,
                       data: List[Dict[str, Any]],
                       output_path: str,
                       sheet_name: str | None = None) -> str:
        """
        用字典列表填充模板（按列名匹配）

        Args:
            data: 字典列表，key 为列名
        """
        config = self.analyze(template_path)
        sheet_tpl = config.get_sheet(sheet_name)
        if not sheet_tpl:
            raise ValueError(f"未找到工作表: {sheet_name}")

        # 构建列名→索引映射
        col_map = {}
        for col in sheet_tpl.columns:
            col_map[col.header] = col.col_index

        # 转换为二维列表
        rows = []
        for item in data:
            row = []
            for col in sheet_tpl.columns:
                row.append(item.get(col.header, None))
            rows.append(row)

        return self.fill_template(template_path, rows, output_path, sheet_name)

    def apply_to_service(self, service, config: ExcelTemplateConfig,
                         sheet_name: str | None = None, data_rows: int | None = None):
        """将模板格式应用到 ExcelService"""
        specs = self.get_format_specs(config, sheet_name, data_rows)
        for spec in specs:
            service.apply_format(spec, sheet_name)

        sheet = config.get_sheet(sheet_name)
        if sheet:
            # 列宽
            for col in sheet.columns:
                service.set_column_width(col.col_letter, col.width, sheet_name)

            # 冻结
            if sheet.freeze_panes:
                service.freeze_header(sheet_name)

            # 筛选
            if sheet.auto_filter:
                service.add_filter(sheet_name)

    # ==========================================
    # 内部：应用样式
    # ==========================================

    def _apply_style(self, cell, style: CellStyleInfo):
        """将 CellStyleInfo 应用到单元格"""
        cell.font = Font(
            name=style.font_name,
            size=style.font_size,
            bold=style.bold,
            italic=style.italic,
            color=style.font_color if style.font_color and style.font_color != "000000" else None,
        )

        if style.bg_color:
            cell.fill = PatternFill(
                start_color=style.bg_color,
                end_color=style.bg_color,
                fill_type="solid",
            )

        cell.alignment = Alignment(
            horizontal=style.alignment,
            vertical=style.vertical_alignment,
            wrap_text=style.wrap_text,
        )

        # 边框
        if style.border.has_border():
            border_kwargs = {}
            for side in ["left", "right", "top", "bottom"]:
                side_style = getattr(style.border, f"{side}_style")
                side_color = getattr(style.border, f"{side}_color")
                if side_style:
                    border_kwargs[side] = Side(
                        style=side_style,
                        color=side_color or "000000",
                    )
            cell.border = Border(**border_kwargs)

    def _clear_data_rows(self, ws, sheet_tpl: SheetTemplate):
        """清除数据行（保留表头和格式区域）"""
        if sheet_tpl.row_count < sheet_tpl.data_start_row:
            return

        # 删除数据行内容（保留格式通过样式重新应用）
        for row_idx in range(sheet_tpl.data_start_row, sheet_tpl.row_count + 1):
            for col_idx in range(1, sheet_tpl.col_count + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                if isinstance(cell, MergedCell):
                    continue  # 合并从属单元格只读，直接写入会抛异常
                cell.value = None

    def _apply_zebra(self, ws, sheet_tpl: SheetTemplate, data_count: int):
        """应用斑马纹"""
        zebra_fill = PatternFill(
            start_color=sheet_tpl.zebra_color,
            end_color=sheet_tpl.zebra_color,
            fill_type="solid",
        )
        for i in range(data_count):
            row_num = sheet_tpl.data_start_row + i
            if i % 2 == 1:  # 奇数行（第2、4、6...条数据）
                for col_idx in range(1, sheet_tpl.col_count + 1):
                    ws.cell(row=row_num, column=col_idx).fill = zebra_fill


# ==========================================
# 便捷函数
# ==========================================

def analyze_excel_template(file_path: str) -> ExcelTemplateConfig:
    """分析 Excel 模板"""
    return ExcelTemplateAnalyzer().analyze(file_path)


def fill_template(template_path: str, data: List[List],
                  output_path: str, sheet_name: str | None = None,
                  headers: List[str] | None = None) -> str:
    """用新数据填充模板"""
    return ExcelTemplateAnalyzer().fill_template(
        template_path, data, output_path, sheet_name, headers
    )
