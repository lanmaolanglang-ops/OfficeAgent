"""
Formula Generator - Excel 公式生成器

将自然语言需求转换为 Excel 公式，并自动写入文件。

支持函数：
- 聚合：SUM, AVERAGE, COUNT, COUNTA, MAX, MIN, MEDIAN, STDEV, PRODUCT
- 条件：SUMIF, SUMIFS, COUNTIF, COUNTIFS, AVERAGEIF, AVERAGEIFS
- 查找：VLOOKUP, XLOOKUP, HLOOKUP, INDEX, MATCH, INDEX+MATCH
- 逻辑：IF, IFS
- 数学：增长率(环比/同比), 占比, 排名, 累计, 去重计数
- 日期：YEAR, MONTH, DAY, DATEDIF, TODAY, EOMONTH
"""
import logging
import re
from typing import Optional, List, Dict, Tuple
from .models import FormulaSpec, DataProfile, ColumnInfo, SheetInfo

logger = logging.getLogger(__name__)


# ==========================================
# 函数模板定义
# ==========================================

class FormulaTemplate:
    """公式模板"""
    def __init__(self, name: str, patterns: List[str], template: str,
                 category: str, description: str, row_wise: bool = False,
                 target_col_offset: int = 0):
        self.name = name
        self.patterns = patterns        # 正则匹配模式
        self.template = template        # 公式模板
        self.category = category        # aggregate/conditional/lookup/logical/math
        self.description = description
        self.row_wise = row_wise        # 是否逐行生成（每行一个公式）
        self.target_col_offset = target_col_offset  # 结果列偏移（0=当前列右侧）


FORMULA_TEMPLATES: Dict[str, FormulaTemplate] = {
    # ========== 聚合函数 ==========
    "sum": FormulaTemplate(
        "sum",
        [r"求和", r"合计", r"总计", r"总和", r"加总", r"汇总", r"sum", r"一共"],
        "=SUM({range})",
        "aggregate", "求和",
    ),
    "average": FormulaTemplate(
        "average",
        [r"平均", r"均值", r"average", r"avg"],
        "=AVERAGE({range})",
        "aggregate", "平均值",
    ),
    "count": FormulaTemplate(
        "count",
        [r"计数", r"数字个数", r"count\("],
        "=COUNT({range})",
        "aggregate", "数字计数",
    ),
    "counta": FormulaTemplate(
        "counta",
        [r"非空个数", r"记录数", r"counta", r"多少条", r"多少行", r"总个数"],
        "=COUNTA({range})",
        "aggregate", "非空计数",
    ),
    "max": FormulaTemplate(
        "max",
        [r"最大", r"最高", r"max", r"峰值", r"最多"],
        "=MAX({range})",
        "aggregate", "最大值",
    ),
    "min": FormulaTemplate(
        "min",
        [r"最小", r"最低", r"min", r"最少"],
        "=MIN({range})",
        "aggregate", "最小值",
    ),
    "median": FormulaTemplate(
        "median",
        [r"中位数", r"median"],
        "=MEDIAN({range})",
        "aggregate", "中位数",
    ),
    "stdev": FormulaTemplate(
        "stdev",
        [r"标准差", r"stdev", r"波动", r"方差"],
        "=STDEV({range})",
        "aggregate", "标准差",
    ),

    # ========== 条件聚合 ==========
    "sumif": FormulaTemplate(
        "sumif",
        [r"按.{0,8}(求和|统计|汇总|计算|总额|合计)",
         r"各.{0,8}(的).{0,4}(求和|总额|合计|销售额|金额|总和|总额|费用|成本|利润)",
         r"条件求和", r"sumif", r"分类汇总", r"分组求和",
         r"分.{0,4}统计", r"每个.{0,8}的.{0,4}(和|总额|合计|金额)"],
        "=SUMIF({criteria_range},{criteria},{sum_range})",
        "conditional", "条件求和",
    ),
    "sumifs": FormulaTemplate(
        "sumifs",
        [r"多条件求和", r"sumifs", r"同时满足.{0,10}求和",
         r"按.{0,4}和.{0,4}.*(求和|统计)"],
        "=SUMIFS({sum_range},{criteria_range1},{criteria1},{criteria_range2},{criteria2})",
        "conditional", "多条件求和",
    ),
    "countif": FormulaTemplate(
        "countif",
        [r"按.{0,8}计数", r"按.{0,8}统计.{0,6}(个数|数量|人数|次数|多少)",
         r"各.{0,8}(的).{0,4}(个数|数量|人数|次数|记录数|多少)",
         r"每个.{0,8}(有|的).{0,4}(个数|数量|人数|次数)",
         r"条件计数", r"countif", r"统计.{0,6}个数",
         r"有多少个", r"有多少", r"多少(个|条|人|次)"],
        "=COUNTIF({criteria_range},{criteria})",
        "conditional", "条件计数",
    ),
    "countifs": FormulaTemplate(
        "countifs",
        [r"多条件计数", r"countifs", r"同时满足.{0,10}计数"],
        "=COUNTIFS({criteria_range1},{criteria1},{criteria_range2},{criteria2})",
        "conditional", "多条件计数",
    ),
    "averageif": FormulaTemplate(
        "averageif",
        [r"按.{0,6}平均", r"条件平均", r"averageif", r"各.{0,4}的.{0,4}平均"],
        "=AVERAGEIF({criteria_range},{criteria},{avg_range})",
        "conditional", "条件平均",
    ),

    # ========== 查找函数 ==========
    "vlookup": FormulaTemplate(
        "vlookup",
        [r"vlookup", r"垂直查找", r"根据.{0,8}查找", r"根据.{0,8}获取",
         r"匹配.{0,8}对应的", r"查找.{0,8}的"],
        "=VLOOKUP({lookup_value},{table_array},{col_index_num},FALSE)",
        "lookup", "垂直查找",
    ),
    "xlookup": FormulaTemplate(
        "xlookup",
        [r"xlookup", r"精确查找", r"反向查找"],
        "=XLOOKUP({lookup_value},{lookup_array},{return_array})",
        "lookup", "XLOOKUP查找",
    ),
    "index_match": FormulaTemplate(
        "index_match",
        [r"index.*match", r"双向查找", r"行列查找"],
        "=INDEX({return_array},MATCH({lookup_value},{lookup_array},0))",
        "lookup", "INDEX+MATCH查找",
    ),
    "match": FormulaTemplate(
        "match",
        [r"match\(", r"位置查找", r"在第几位"],
        "=MATCH({lookup_value},{lookup_array},0)",
        "lookup", "位置查找",
    ),

    # ========== 逻辑函数 ==========
    "if": FormulaTemplate(
        "if",
        [r"如果", r"判断", r"是否", r"\bif\b"],
        "=IF({condition},{true_val},{false_val})",
        "logical", "条件判断",
    ),
    "ifs": FormulaTemplate(
        "ifs",
        [r"多条件判断", r"ifs\(", r"分级", r"分档"],
        "=IFS({cond1},{val1},{cond2},{val2},TRUE,{default})",
        "logical", "多条件判断",
    ),

    # ========== 数学/统计（逐行） ==========
    "growth_mom": FormulaTemplate(
        "growth_mom",
        [r"环比增长", r"月环比", r"环比", r"mom", r"与上月相比",
         r"本月.*较上月", r"较上期"],
        "=IFERROR(({cur}-{prev})/{prev},\"\")",
        "math", "环比增长率",
        row_wise=True, target_col_offset=1,
    ),
    "growth_yoy": FormulaTemplate(
        "growth_yoy",
        [r"同比增长", r"年同比", r"同比", r"yoy", r"与去年相比",
         r"较上年", r"同期增长"],
        "=IFERROR(({cur}-{prev_year})/{prev_year},\"\")",
        "math", "同比增长率",
        row_wise=True, target_col_offset=1,
    ),
    "growth": FormulaTemplate(
        "growth",
        [r"增长率", r"增长", r"增幅", r"growth", r"较前"],
        "=IFERROR(({cur}-{prev})/{prev},\"\")",
        "math", "增长率",
        row_wise=True, target_col_offset=1,
    ),
    "percent_of_total": FormulaTemplate(
        "percent_of_total",
        [r"占比", r"占总", r"百分比", r"proportion", r"份额", r"比重"],
        "=IFERROR({cell}/SUM({range}),0)",
        "math", "占比",
        row_wise=True, target_col_offset=1,
    ),
    "rank": FormulaTemplate(
        "rank",
        [r"排名", r"名次", r"rank", r"排第几", r"排序位置"],
        "=RANK({cell},{range},0)",
        "math", "排名",
        row_wise=True, target_col_offset=1,
    ),
    "cumulative": FormulaTemplate(
        "cumulative",
        [r"累计", r"累积", r"cumulative", r" running total", r"到目前为止"],
        "=SUM({start_cell}:{cur_cell})",
        "math", "累计求和",
        row_wise=True, target_col_offset=1,
    ),
    "dedup_count": FormulaTemplate(
        "dedup_count",
        [r"去重计数", r"不重复", r"唯一值", r"distinct", r"unique count"],
        '=SUMPRODUCT(({range}<>"")/COUNTIF({range},{range}&""))',
        "math", "去重计数",
    ),
}


class FormulaGenerator:
    """
    Excel 公式生成器

    用法:
        gen = FormulaGenerator(profile)

        # 自然语言生成
        formulas = gen.generate_from_text("计算每个月销售增长率", "销售数据")

        # 自动写入 Excel
        gen.apply_to_file("data.xlsx", "计算销售合计和平均", output_path="result.xlsx")
    """

    def __init__(self, profile: DataProfile | None = None):
        self.profile = profile

    def set_profile(self, profile: DataProfile):
        self.profile = profile

    # ==========================================
    # 自然语言 → 公式
    # ==========================================

    def generate_from_text(self, text: str,
                           sheet_name: str | None = None,
                           data_start_row: int = 2) -> List[FormulaSpec]:
        """
        从自然语言生成公式列表

        支持的表达：
        - "计算销售额的合计和平均" → SUM, AVERAGE
        - "按城市统计销售额" → SUMIF
        - "计算每月环比增长率" → 逐行增长率公式
        - "计算各产品销售额占比" → 逐行占比公式
        - "销售额排名" → RANK
        - "根据产品ID查找价格" → VLOOKUP
        """
        formulas = []
        sheet = self.profile.get_sheet(sheet_name) if self.profile else None
        # 1. 识别函数类型
        detected_types = self._detect_formula_types(text)
        # “去重计数”会同时命中泛化的“计数”，仅保留更具体的语义。
        if "dedup_count" in detected_types:
            detected_types = [t for t in detected_types if t != "count"]

        # 2. 识别目标列
        target_cols = self._detect_target_columns(text, sheet)
        # 识别条件列（用于 SUMIF/COUNTIF）
        condition_col = self._detect_condition_column(text, sheet)
        # 识别查找相关列
        lookup_info = self._detect_lookup_info(text, sheet)

        # 3. 逐类生成
        has_conditional = any(
            FORMULA_TEMPLATES[t].category == "conditional" for t in detected_types
        )
        has_row_wise = any(
            FORMULA_TEMPLATES[t].row_wise for t in detected_types
        )

        # 跟踪逐行公式已使用的结果列
        next_result_col = sheet.col_count if sheet else 10
        aggregate_row_offset = 0

        for ftype in detected_types:
            tpl = FORMULA_TEMPLATES[ftype]

            # 如果有条件聚合，跳过普通聚合（避免重复）
            if has_conditional and tpl.category == "aggregate":
                continue
            # 如果有逐行公式，跳过普通聚合
            if has_row_wise and tpl.category == "aggregate" and ftype in ("sum", "average"):
                continue

            if tpl.row_wise:
                # 逐行公式
                row_formulas = self._generate_row_wise(
                    tpl, target_cols, sheet, data_start_row, text, next_result_col
                )
                formulas.extend(row_formulas)
                # 每个目标列用一个结果列
                next_result_col += len(target_cols) if target_cols else 1

            elif tpl.category == "conditional":
                # 条件聚合
                cond_formulas = self._generate_conditional(
                    tpl, target_cols, condition_col, sheet, data_start_row
                )
                formulas.extend(cond_formulas)

            elif tpl.category == "lookup":
                # 查找公式
                lookup_formulas = self._generate_lookup(
                    tpl, lookup_info, target_cols, sheet, data_start_row
                )
                formulas.extend(lookup_formulas)

            elif tpl.category == "aggregate":
                # 聚合函数
                agg_formulas = self._generate_aggregate(
                    tpl, target_cols, sheet, data_start_row,
                    result_row_offset=aggregate_row_offset,
                )
                formulas.extend(agg_formulas)
                if agg_formulas:
                    aggregate_row_offset += 1

            elif tpl.category == "logical":
                logical_formulas = self._generate_logical(
                    tpl, target_cols, sheet, data_start_row, text
                )
                formulas.extend(logical_formulas)

            elif tpl.category == "math":
                # 非逐行数学模板（当前为去重计数）按整列聚合输出。
                math_formulas = self._generate_aggregate(
                    tpl, target_cols, sheet, data_start_row,
                    result_row_offset=aggregate_row_offset,
                )
                formulas.extend(math_formulas)
                if math_formulas:
                    aggregate_row_offset += 1

        # 去重
        seen = set()
        unique = []
        for f in formulas:
            key = (f.target_cell, f.formula)
            if key not in seen:
                seen.add(key)
                unique.append(f)

        return unique

    # ==========================================
    # 各类公式生成
    # ==========================================

    def _generate_aggregate(self, tpl: FormulaTemplate,
                             target_cols: List[ColumnInfo],
                             sheet: SheetInfo | None,
                             data_start_row: int,
                             result_row_offset: int = 0) -> List[FormulaSpec]:
        """生成聚合公式（SUM/AVERAGE/COUNT等）"""
        formulas: List[FormulaSpec] = []
        if sheet and sheet.row_count <= 0:
            return formulas
        if not target_cols and sheet:
            target_cols = [c for c in sheet.columns if c.data_type == "number"]
        if not target_cols:
            return formulas

        end_row = data_start_row + (sheet.row_count if sheet else 100) - 1

        for col in target_cols:
            col_letter = self._col_letter(col.index)
            range_str = f"{col_letter}{data_start_row}:{col_letter}{end_row}"
            formula = tpl.template.format(range=range_str)

            # 找一个空行放结果
            # 同一种聚合的各列放在同一行；不同聚合由调用方全局错行。
            result_row = end_row + 2 + result_row_offset
            target_cell = f"{col_letter}{result_row}"

            formulas.append(FormulaSpec(
                formula=formula,
                target_cell=target_cell,
                description=f"{col.name}{tpl.description}",
                category=tpl.name,
            ))

        return formulas

    def _generate_row_wise(self, tpl: FormulaTemplate,
                            target_cols: List[ColumnInfo],
                            sheet: SheetInfo | None,
                            data_start_row: int,
                            text: str = "",
                            start_result_col: int = 0) -> List[FormulaSpec]:
        """生成逐行公式（增长率/占比/排名/累计）"""
        formulas: List[FormulaSpec] = []
        if sheet and sheet.row_count <= 0:
            return formulas
        if not target_cols and sheet:
            target_cols = [c for c in sheet.columns if c.data_type == "number"]
        if not target_cols:
            return formulas

        end_row = data_start_row + (sheet.row_count if sheet else 100) - 1

        for col_offset, col in enumerate(target_cols):
            col_letter = self._col_letter(col.index)
            # 结果列写到数据右侧
            result_col_idx = start_result_col + col_offset
            result_col_letter = self._col_letter(result_col_idx)
            range_str = f"{col_letter}{data_start_row}:{col_letter}{end_row}"

            if tpl.name in ("growth", "growth_mom", "growth_yoy"):
                # 环比取上一期；同比按数据粒度取上一年同期，不能与环比共用上一行。
                lag = 1
                if tpl.name == "growth_yoy":
                    if re.search(r"季度|季报|\bq[1-4]\b", text, re.IGNORECASE):
                        lag = 4
                    elif re.search(r"周|weekly", text, re.IGNORECASE):
                        lag = 52
                    elif re.search(r"日|daily", text, re.IGNORECASE):
                        lag = 365
                    elif re.search(r"年度|按年|每年|yearly", text, re.IGNORECASE):
                        lag = 1
                    else:
                        # 月度是业务报表中最常见的同比粒度；没有明确粒度时采用 12 期。
                        lag = 12
                for row in range(data_start_row + lag, end_row + 1):
                    cur = f"{col_letter}{row}"
                    prev = f"{col_letter}{row - 1}"
                    prev_year = f"{col_letter}{row - lag}"
                    formula = tpl.template.format(cur=cur, prev=prev, prev_year=prev_year)
                    formulas.append(FormulaSpec(
                        formula=formula,
                        target_cell=f"{result_col_letter}{row}",
                        description=f"{col.name}{tpl.description}",
                        category=tpl.name,
                    ))
                # 第一行设为空或标题
                formulas.append(FormulaSpec(
                    formula=tpl.description,
                    target_cell=f"{result_col_letter}{data_start_row}",
                    description=f"{col.name}{tpl.description}标题",
                    category="header",
                ))

            elif tpl.name == "percent_of_total":
                # 占比：每行 / SUM(数据区)。分母用 SUM 自包含，
                # 不依赖外部合计行（原实现对空单元格除法得到全 0）
                for row in range(data_start_row, end_row + 1):
                    cell = f"{col_letter}{row}"
                    formula = tpl.template.format(cell=cell, range=range_str)
                    formulas.append(FormulaSpec(
                        formula=formula,
                        target_cell=f"{result_col_letter}{row}",
                        description=f"{col.name}{tpl.description}",
                        category=tpl.name,
                    ))

            elif tpl.name == "rank":
                # 排名
                for row in range(data_start_row, end_row + 1):
                    cell = f"{col_letter}{row}"
                    formula = tpl.template.format(cell=cell, range=range_str)
                    formulas.append(FormulaSpec(
                        formula=formula,
                        target_cell=f"{result_col_letter}{row}",
                        description=f"{col.name}{tpl.description}",
                        category=tpl.name,
                    ))

            elif tpl.name == "cumulative":
                # 累计
                for row in range(data_start_row, end_row + 1):
                    start = f"{col_letter}{data_start_row}"
                    cur = f"{col_letter}{row}"
                    formula = tpl.template.format(start_cell=start, cur_cell=cur)
                    formulas.append(FormulaSpec(
                        formula=formula,
                        target_cell=f"{result_col_letter}{row}",
                        description=f"{col.name}{tpl.description}",
                        category=tpl.name,
                    ))

        return formulas

    def _generate_conditional(self, tpl: FormulaTemplate,
                               target_cols: List[ColumnInfo],
                               condition_col: Optional[ColumnInfo],
                               sheet: SheetInfo | None,
                               data_start_row: int) -> List[FormulaSpec]:
        """生成条件聚合公式（SUMIF/COUNTIF/SUMIFS等）"""
        formulas: List[FormulaSpec] = []
        if not sheet:
            return formulas

        end_row = data_start_row + sheet.row_count - 1

        # 条件列
        if condition_col is None:
            # 找第一个分类列
            cat_cols = [c for c in sheet.columns
                       if c.semantic_type in ("category", "name", "id")
                       and c.data_type == "text"]
            if not cat_cols:
                cat_cols = [c for c in sheet.columns if c.data_type == "text"]
            if not cat_cols:
                return formulas
            condition_col = cat_cols[0]

        cond_letter = self._col_letter(condition_col.index)
        cond_range = f"{cond_letter}{data_start_row}:{cond_letter}{end_row}"

        # 目标列（求和/计数/平均的列）
        if tpl.name in ("countif", "countifs"):
            # COUNTIF 只对条件列计数，不需要数值列
            target_cols = [condition_col]
        elif not target_cols:
            target_cols = [c for c in sheet.columns
                          if c.data_type == "number" and c.index != condition_col.index]

        # 获取条件列的唯一值
        unique_values = self._get_unique_values(sheet, condition_col)

        result_row = end_row + 3
        for val_idx, val in enumerate(unique_values[:20]):  # 最多20个分类
            # Excel 字符串字面量中的双引号必须双写。
            criteria = f'"{val.replace(chr(34), chr(34) * 2)}"' if isinstance(val, str) else str(val)

            for col in target_cols:
                col_letter = self._col_letter(col.index)
                sum_range = f"{col_letter}{data_start_row}:{col_letter}{end_row}"

                if tpl.name == "sumif":
                    formula = f'=SUMIF({cond_range},{criteria},{sum_range})'
                elif tpl.name == "countif":
                    formula = f'=COUNTIF({cond_range},{criteria})'
                    # COUNTIF 结果写在条件列右侧
                    col_letter = self._col_letter(condition_col.index + 1)
                elif tpl.name == "averageif":
                    formula = f'=AVERAGEIF({cond_range},{criteria},{sum_range})'
                elif tpl.name == "sumifs":
                    formula = f'=SUMIFS({sum_range},{cond_range},{criteria})'
                elif tpl.name == "countifs":
                    formula = f'=COUNTIFS({cond_range},{criteria})'
                    col_letter = self._col_letter(condition_col.index + 1)
                else:
                    formula = tpl.template.format(
                        criteria_range=cond_range,
                        criteria=criteria,
                        sum_range=sum_range,
                        avg_range=sum_range,
                    )

                target_cell = f"{col_letter}{result_row + val_idx}"
                formulas.append(FormulaSpec(
                    formula=formula,
                    target_cell=target_cell,
                    description=f"{condition_col.name}={val} 的{col.name}{tpl.description}",
                    category=tpl.name,
                ))

            # 在条件列写分类标签
            label_cell = f"{cond_letter}{result_row + val_idx}"
            formulas.append(FormulaSpec(
                formula=str(val),
                target_cell=label_cell,
                description=f"分类标签: {val}",
                category="label",
            ))

        return formulas

    def _generate_lookup(self, tpl: FormulaTemplate,
                          lookup_info: dict,
                          target_cols: List[ColumnInfo],
                          sheet: SheetInfo | None,
                          data_start_row: int) -> List[FormulaSpec]:
        """生成查找公式（VLOOKUP/XLOOKUP/INDEX+MATCH）"""
        formulas: List[FormulaSpec] = []
        if not lookup_info or not sheet:
            return formulas

        end_row = data_start_row + sheet.row_count - 1
        lookup_col = lookup_info.get("lookup_col")
        return_col = lookup_info.get("return_col")

        if not lookup_col:
            return formulas

        last_col_idx = sheet.col_count - 1
        lookup_idx = lookup_col.index
        lookup_letter = self._col_letter(lookup_idx)

        # 返回列：优先识别结果，其次目标列，最后退回查找列右邻列
        # （等价于历史"从 A 查、取 B"的语义，但不写死 A）。
        if return_col is not None:
            return_idx = return_col.index
        elif target_cols:
            return_idx = target_cols[0].index
        else:
            return_idx = lookup_idx + 1
        if return_idx > last_col_idx:
            # 查找列已是最后一列，没有可返回的数据列
            return formulas
        return_letter = self._col_letter(return_idx)
        return_name = return_col.name if return_col else ""

        # 在数据右侧生成查找公式
        result_col = self._col_letter(sheet.col_count + 1)
        lookup_array = f"{lookup_letter}{data_start_row}:{lookup_letter}{end_row}"
        return_array = f"{return_letter}{data_start_row}:{return_letter}{end_row}"

        if tpl.name == "vlookup":
            built = self._build_vlookup_range(lookup_idx, return_idx, last_col_idx)
            if built is None:
                # 返回列位于查找列左侧：VLOOKUP 结构上无法表达（它只能向右取），
                # 历史上会生成必然 #N/A 的伪公式并被 IFERROR 吞成空值。
                # 这里改用项目既有的 XLOOKUP 反向查找策略。
                for row in range(data_start_row, end_row + 1):
                    formula = (f"=IFERROR(XLOOKUP({lookup_letter}{row},"
                              f"{lookup_array},{return_array}),\"\")")
                    formulas.append(FormulaSpec(
                        formula=formula,
                        target_cell=f"{result_col}{row}",
                        description=f"根据{lookup_col.name}反向查找{return_name}",
                        category="xlookup",
                    ))
            else:
                table_range, col_index_num = built
                for row in range(data_start_row, end_row + 1):
                    formula = (f"=IFERROR(VLOOKUP({lookup_letter}{row},"
                              f"{table_range},{col_index_num},FALSE),\"\")")
                    formulas.append(FormulaSpec(
                        formula=formula,
                        target_cell=f"{result_col}{row}",
                        description=f"根据{lookup_col.name}查找{return_name}",
                        category=tpl.name,
                    ))

        elif tpl.name == "xlookup":
            for row in range(data_start_row, end_row + 1):
                formula = (f"=IFERROR(XLOOKUP({lookup_letter}{row},"
                          f"{lookup_array},{return_array}),\"\")")
                formulas.append(FormulaSpec(
                    formula=formula,
                    target_cell=f"{result_col}{row}",
                    description="XLOOKUP查找",
                    category=tpl.name,
                ))

        elif tpl.name == "index_match":
            for row in range(data_start_row, end_row + 1):
                formula = (f"=IFERROR(INDEX({return_array},"
                          f"MATCH({lookup_letter}{row},{lookup_array},0)),\"\")")
                formulas.append(FormulaSpec(
                    formula=formula,
                    target_cell=f"{result_col}{row}",
                    description="INDEX+MATCH查找",
                    category=tpl.name,
                ))

        return formulas

    def _build_vlookup_range(self, lookup_idx: int, return_idx: int,
                              last_col_idx: int) -> Tuple[str, int] | None:
        """构造 VLOOKUP 的 ``table_array`` 与 ``col_index_num``。

        VLOOKUP 要求查找列是 table_array 的第一列，且只能向右取返回值，
        因此正确的 ``col_index_num`` 是 ``return_idx - lookup_idx + 1``
        而不是 ``return_idx + 1``（P1-10）。

        返回 None 表示返回列位于查找列左侧、VLOOKUP 无法表达，调用方应
        回退到 XLOOKUP / INDEX+MATCH。
        """
        if return_idx < lookup_idx:
            return None
        start_letter = self._col_letter(lookup_idx)
        end_letter = self._col_letter(max(return_idx, last_col_idx))
        return f"{start_letter}:{end_letter}", return_idx - lookup_idx + 1

    def _generate_logical(self, tpl: FormulaTemplate,
                           target_cols: List[ColumnInfo],
                           sheet: SheetInfo | None,
                           data_start_row: int,
                           text: str = "") -> List[FormulaSpec]:
        """生成逻辑公式"""
        formulas: List[FormulaSpec] = []
        if not target_cols or not sheet or sheet.row_count <= 0:
            return formulas

        end_row = data_start_row + sheet.row_count - 1
        col = target_cols[0]
        col_letter = self._col_letter(col.index)
        # 逻辑结果必须写到数据区右侧，不能覆盖目标列旁边的用户数据。
        result_col = self._col_letter(sheet.col_count)

        # 尝试从自然语言解析条件；IFS 至少保留显式兜底分支。
        condition, true_val, false_val = self._parse_if_condition(text, col)
        ifs_branches, ifs_default = self._parse_ifs_branches(text)

        for row in range(data_start_row, end_row + 1):
            cell = f"{col_letter}{row}"
            cond = condition.replace("{cell}", cell) if condition else f"{cell}>0"
            tv = true_val.replace("{cell}", cell) if true_val else '"达标"'
            fv = false_val.replace("{cell}", cell) if false_val else '"未达标"'
            if tpl.name == "ifs" and ifs_branches:
                parts = []
                for branch_condition, branch_value in ifs_branches:
                    parts.extend([
                        branch_condition.replace("{cell}", cell),
                        self._formula_value(branch_value),
                    ])
                parts.extend(["TRUE", self._formula_value(ifs_default)])
                formula = f'=IFS({",".join(parts)})'
            elif tpl.name == "ifs":
                formula = f'=IFS({cond},{tv},TRUE,{fv})'
            else:
                formula = f'=IF({cond},{tv},{fv})'
            formulas.append(FormulaSpec(
                formula=formula,
                target_cell=f"{result_col}{row}",
                description=f"判断{col.name}",
                category=tpl.name,
            ))

        return formulas

    # ==========================================
    # 便捷生成方法
    # ==========================================

    def generate_summary_row(self, sheet: SheetInfo,
                              sum_cols: List[int] | None = None,
                              data_start_row: int = 2,
                              summary_row: int | None = None) -> List[FormulaSpec]:
        """生成汇总行（合计/平均/最大/最小）"""
        formulas = []
        end_row = data_start_row + sheet.row_count - 1
        if summary_row is None:
            summary_row = end_row + 1

        if sum_cols is None:
            sum_cols = [c.index for c in sheet.columns if c.data_type == "number"]

        for col_idx in sum_cols:
            col_letter = self._col_letter(col_idx)
            range_str = f"{col_letter}{data_start_row}:{col_letter}{end_row}"
            formulas.append(FormulaSpec(
                formula=f"=SUM({range_str})",
                target_cell=f"{col_letter}{summary_row}",
                description=f"{sheet.columns[col_idx].name}合计",
                category="sum",
            ))

        return formulas

    def generate_growth_formulas(self, col_idx: int, sheet: SheetInfo,
                                  data_start_row: int = 2,
                                  growth_type: str = "mom") -> List[FormulaSpec]:
        """生成增长率公式（逐行）"""
        formulas = []
        end_row = data_start_row + sheet.row_count - 1
        col_letter = self._col_letter(col_idx)
        result_col = self._col_letter(sheet.col_count)

        label = "环比增长率" if growth_type == "mom" else "同比增长率"
        formulas.append(FormulaSpec(
            formula=label,
            target_cell=f"{result_col}{data_start_row}",
            description=label,
            category="header",
        ))

        for row in range(data_start_row + 1, end_row + 1):
            cur = f"{col_letter}{row}"
            prev = f"{col_letter}{row - 1}"
            formulas.append(FormulaSpec(
                formula=f'=IFERROR(({cur}-{prev})/{prev},"")',
                target_cell=f"{result_col}{row}",
                description=f"第{row}行{label}",
                category="growth",
            ))

        return formulas

    def generate_rank_formulas(self, col_idx: int, sheet: SheetInfo,
                                data_start_row: int = 2) -> List[FormulaSpec]:
        """生成排名公式"""
        formulas = []
        end_row = data_start_row + sheet.row_count - 1
        col_letter = self._col_letter(col_idx)
        result_col = self._col_letter(sheet.col_count)
        range_str = f"{col_letter}{data_start_row}:{col_letter}{end_row}"

        for row in range(data_start_row, end_row + 1):
            formulas.append(FormulaSpec(
                formula=f"=RANK({col_letter}{row},{range_str},0)",
                target_cell=f"{result_col}{row}",
                description=f"第{row}行排名",
                category="rank",
            ))

        return formulas

    def generate_vlookup_formulas(self, lookup_col_idx: int,
                                   return_col_idx: int,
                                   sheet: SheetInfo,
                                   data_start_row: int = 2) -> List[FormulaSpec]:
        """生成 VLOOKUP 公式"""
        formulas = []
        end_row = data_start_row + sheet.row_count - 1
        lookup_letter = self._col_letter(lookup_col_idx)
        # VLOOKUP 的 table_array 必须从真正的查找列起，col_index_num 也必须是
        # 相对查找列的偏移；写死 A 列会让非 A 列查找恒为 #N/A（P1-10）。
        built = self._build_vlookup_range(
            lookup_col_idx, return_col_idx, sheet.col_count - 1
        )
        if built is None:
            # 返回列在查找列左侧，VLOOKUP 无法表达：回退 XLOOKUP
            return self.generate_xlookup_formulas(
                lookup_col_idx, return_col_idx, sheet, data_start_row
            )
        table_range, col_index_num = built

        for row in range(data_start_row, end_row + 1):
            formulas.append(FormulaSpec(
                formula=f"=IFERROR(VLOOKUP({lookup_letter}{row},{table_range},{col_index_num},FALSE),\"\")",
                target_cell=f"{self._col_letter(sheet.col_count + 1)}{row}",
                description="VLOOKUP查找",
                category="vlookup",
            ))

        return formulas

    def generate_xlookup_formulas(self, lookup_col_idx: int,
                                    return_col_idx: int,
                                    sheet: SheetInfo,
                                    data_start_row: int = 2) -> List[FormulaSpec]:
        """生成 XLOOKUP 公式（VLOOKUP 无法表达的反向查找回退方案）"""
        formulas = []
        end_row = data_start_row + sheet.row_count - 1
        lookup_letter = self._col_letter(lookup_col_idx)
        return_letter = self._col_letter(return_col_idx)
        lookup_array = f"{lookup_letter}{data_start_row}:{lookup_letter}{end_row}"
        return_array = f"{return_letter}{data_start_row}:{return_letter}{end_row}"
        for row in range(data_start_row, end_row + 1):
            formulas.append(FormulaSpec(
                formula=(f"=IFERROR(XLOOKUP({lookup_letter}{row},"
                         f"{lookup_array},{return_array}),\"\")"),
                target_cell=f"{self._col_letter(sheet.col_count + 1)}{row}",
                description="XLOOKUP查找",
                category="xlookup",
            ))
        return formulas

    # ==========================================
    # 自动写入 Excel
    # ==========================================

    def apply_to_sheet(self, service, sheet_name: str,
                        formulas: List[FormulaSpec]) -> int:
        """将公式写入工作表（通过 ExcelService）"""
        count = 0
        for f in formulas:
            if f.category == "header" or f.category == "label":
                # 标题/标签直接写值
                service.set_cell(sheet_name, f.target_cell, f.formula)
            else:
                service.set_cell(sheet_name, f.target_cell, f.formula)
            count += 1
        return count

    def apply_to_file(self, file_path: str, text: str,
                       sheet_name: str | None = None,
                       output_path: str | None = None) -> Tuple[str, List[FormulaSpec]]:
        """
        从自然语言生成公式并写入 Excel 文件

        Returns:
            (output_path, formulas)
        """
        from .excel_service import ExcelService, _derive_output_path

        service = ExcelService()
        service.open(file_path)

        if sheet_name is None:
            sheet_name = service.workbook.sheetnames[0]

        # 分析数据
        if self.profile is None:
            try:
                from .data_analyzer import DataAnalyzer
            except ImportError:
                logger.debug("DataAnalyzer 不可用，按无画像继续", exc_info=True)
            else:
                try:
                    self.profile = DataAnalyzer().analyze(file_path)
                except Exception:
                    # pandas/openpyxl 第三方边界：画像失败不阻塞公式生成，
                    # 但必须留痕（此前静默 pass 会吞掉编程错误）。
                    logger.warning(
                        f"数据画像分析失败，按无画像继续: {file_path}",
                        exc_info=True,
                    )

        # 生成公式
        formulas = self.generate_from_text(text, sheet_name)

        # 写入
        self.apply_to_sheet(service, sheet_name, formulas)

        if output_path is None:
            output_path = _derive_output_path(file_path, "_formulas")

        service.save(output_path)
        return output_path, formulas

    # ==========================================
    # 解析辅助方法
    # ==========================================

    def _detect_formula_types(self, text: str) -> List[str]:
        """识别文本中需要的公式类型"""
        detected = []
        for name, tpl in FORMULA_TEMPLATES.items():
            for pattern in tpl.patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    detected.append(name)
                    break

        # 优先级：增长率 > 条件计数 > 条件求和 > 查找 > 聚合
        priority = {
            "growth_mom": 1, "growth_yoy": 1, "growth": 2,
            "countifs": 3, "countif": 3,
            "sumifs": 4, "sumif": 4, "averageif": 4,
            "xlookup": 5, "index_match": 5, "vlookup": 6,
            "ifs": 7, "if": 8,
            "percent_of_total": 9, "rank": 9, "cumulative": 9, "dedup_count": 9,
            "sum": 10, "average": 10, "count": 10, "counta": 10,
            "max": 10, "min": 10, "median": 10, "stdev": 10,
        }
        detected.sort(key=lambda x: priority.get(x, 99))

        return detected if detected else ["sum", "average"]

    def _detect_target_columns(self, text: str,
                                sheet: SheetInfo | None = None) -> List[ColumnInfo]:
        """识别目标列（要计算的列）"""
        if not sheet:
            return []

        matched = []
        for col in sheet.columns:
            if col.name and col.name in text:
                if col.data_type == "number" or col.semantic_type in ("amount", "quantity", "metric"):
                    matched.append(col)

        if not matched:
            # 模糊匹配：金额/数量/指标列
            for col in sheet.columns:
                if col.semantic_type in ("amount", "quantity", "metric"):
                    for kw in ["金额", "额", "数量", "销售", "收入", "支出", "价格", "分数", "得分"]:
                        if kw in text and (kw in col.name or col.semantic_type in ("amount", "quantity")):
                            matched.append(col)
                            break

        # 去重
        seen = set()
        unique = []
        for c in matched:
            if c.name not in seen:
                seen.add(c.name)
                unique.append(c)

        return unique

    def _detect_condition_column(self, text: str,
                                  sheet: SheetInfo | None = None) -> Optional[ColumnInfo]:
        """识别条件列（按XX统计）"""
        if not sheet:
            return None

        # "按XX" / "各XX" / "分XX" 模式
        for pattern in [r"按(.{2,8}?)(?:的|统计|求和|计数|计算|分组|分类|$)",
                        r"各(.{2,8}?)(?:的|统计|$)",
                        r"分(.{2,8}?)(?:的|统计|$)"]:
            m = re.search(pattern, text)
            if m:
                keyword = m.group(1).strip()
                for col in sheet.columns:
                    if keyword in col.name or col.name in keyword:
                        return col

        # 找分类列
        for col in sheet.columns:
            if col.semantic_type in ("category", "name"):
                return col

        return None

    def _detect_lookup_info(self, text: str,
                             sheet: SheetInfo | None = None) -> dict:
        """识别查找相关信息"""
        if not sheet:
            return {}

        info = {}

        # "根据XX查找YY" / "根据XX获取YY"
        m = re.search(r"根据(.{2,8}?)(?:查找|查询|获取|匹配)(.{0,8}?)(?:的|$)", text)
        if m:
            lookup_kw = m.group(1).strip()
            return_kw = m.group(2).strip() if m.group(2) else ""

            for col in sheet.columns:
                if lookup_kw in col.name or col.name in lookup_kw:
                    info["lookup_col"] = col
                if return_kw and return_kw in col.name:
                    info["return_col"] = col

        return info

    def _parse_if_condition(self, text: str, col: ColumnInfo) -> Tuple[str, str, str]:
        """解析 IF 条件"""
        # "大于X" / ">X" / "超过X"
        condition = "{cell}>0"
        true_val = '"达标"'
        false_val = '"未达标"'

        m = re.search(r"大于\s*(\d+\.?\d*)", text)
        if m:
            threshold = m.group(1)
            condition = f"{{cell}}>{threshold}"

        m = re.search(r"超过\s*(\d+\.?\d*)", text)
        if m:
            threshold = m.group(1)
            condition = f"{{cell}}>{threshold}"

        m = re.search(r"小于\s*(\d+\.?\d*)", text)
        if m:
            threshold = m.group(1)
            condition = f"{{cell}}<{threshold}"

        if "大于等于" in text or "不低于" in text:
            m = re.search(r"(?:大于等于|不低于)\s*(\d+\.?\d*)", text)
            if m:
                condition = f"{{cell}}>={m.group(1)}"

        return condition, true_val, false_val

    def _parse_ifs_branches(self, text: str) -> Tuple[List[Tuple[str, str]], str]:
        """解析“>=90 为优秀，>=60 为及格，否则不及格”一类分级表达。"""
        operator_map = {
            "大于等于": ">=", "不低于": ">=", "大于": ">", "超过": ">",
            "小于等于": "<=", "不高于": "<=", "小于": "<", "低于": "<",
        }
        pair_pattern = re.compile(
            r"(大于等于|不低于|小于等于|不高于|大于|超过|小于|低于)"
            r"\s*(-?\d+(?:\.\d+)?)\s*(?:判定为|显示为|则为|则|为|显示)?\s*"
            r"([^，,；;。]+)"
        )
        branches = []
        for operator, threshold, value in pair_pattern.findall(text):
            cleaned = re.split(r"\s*(?:否则|其余|其他)\s*", value, maxsplit=1)[0].strip()
            if cleaned:
                branches.append((f"{{cell}}{operator_map[operator]}{threshold}", cleaned))

        default_match = re.search(
            r"(?:否则|其余|其他)\s*(?:判定为|显示为|则为|则|为|显示)?\s*([^，,；;。]+)",
            text,
        )
        default = default_match.group(1).strip() if default_match else "未达标"
        return branches, default

    @staticmethod
    def _formula_value(value: str) -> str:
        """把自然语言分级结果安全转换为 Excel 字面量。"""
        value = str(value).strip().strip('"“”')
        if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
            return value
        return f'"{value.replace(chr(34), chr(34) * 2)}"'

    def _get_unique_values(self, sheet: SheetInfo,
                            col: ColumnInfo,
                            max_count: int = 20) -> list:
        """获取列的唯一值；样本值仅作为旧画像的兼容回退。"""
        if col.unique_values:
            return col.unique_values[:max_count]
        if col.sample_values:
            seen = []
            for v in col.sample_values:
                if v not in seen:
                    seen.append(v)
            return seen[:max_count]
        return []

    @staticmethod
    def _col_letter(index: int) -> str:
        """0-based 列索引转 Excel 列字母（统一入口在 models.col_letter）"""
        from .models import col_letter
        return col_letter(index)


# ==========================================
# 便捷函数
# ==========================================

def generate_formulas(text: str, profile: DataProfile | None = None,
                      sheet_name: str | None = None) -> List[FormulaSpec]:
    """便捷函数：从自然语言生成公式"""
    gen = FormulaGenerator(profile)
    return gen.generate_from_text(text, sheet_name)


def apply_formulas(file_path: str, text: str,
                    sheet_name: str | None = None,
                    output_path: str | None = None) -> Tuple[str, List[FormulaSpec]]:
    """便捷函数：生成公式并写入文件"""
    gen = FormulaGenerator()
    return gen.apply_to_file(file_path, text, sheet_name, output_path)
