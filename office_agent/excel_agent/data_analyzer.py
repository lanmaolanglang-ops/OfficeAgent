"""
Data Analyzer - 数据理解器

功能：
1. 读取 .xlsx 文件，识别所有工作表
2. 自动识别表头、字段类型（text/number/date/boolean）
3. 语义分类：日期/金额/数量/分类/指标/ID/名称/百分比
4. 发现表间数据关系
5. 生成 DataSchema JSON
"""
from pathlib import Path
from typing import Optional, List, Dict
from collections import Counter
import re
from datetime import datetime

import pandas as pd

from .models import (
    DataProfile, SheetInfo, ColumnInfo,
    DataSchema, SheetSchema, DataRelation,
)
from .excel_service import ExcelService


# ==========================================
# 语义关键词词典
# ==========================================

SEMANTIC_KEYWORDS = {
    "date": [
        "日期", "时间", "年", "月", "日", "年份", "月份", "季度", "date", "time",
        "year", "month", "day", "quarter", "dt", "create_time", "update_time",
        "订单日期", "下单时间", "成交时间", "开始", "结束",
    ],
    "amount": [
        "金额", "价格", "收入", "支出", "成本", "费用", "销售额", "营业额",
        "利润", "营收", "单价", "总价", "总额", "amount", "price", "revenue",
        "cost", "fee", "salary", "工资", "薪资", "奖金", "税", "预算",
        "成交金额", "支付金额", "应收", "应付", "gmv",
    ],
    "quantity": [
        "数量", "个数", "件数", "人数", "次数", "qty", "quantity", "count",
        "num", "库存", "销量", "购买数", "订单数", "客户数", "用户数",
        "pv", "uv", "点击", "浏览", "访问",
    ],
    "category": [
        "类型", "类别", "分类", "种类", "品类", "等级", "级别", "状态",
        "category", "type", "class", "level", "status", "group", "tag",
        "地区", "区域", "城市", "省份", "部门", "渠道", "来源", "行业",
        "性别", "岗位", "职位", "品牌", "产品类型",
    ],
    "metric": [
        "率", "比", "得分", "评分", "指标", "指数", "占比", "增长率",
        "完成率", "转化率", "满意度", "metric", "rate", "ratio", "score",
        "index", "百分比", "达成率", "progress",
    ],
    "id": [
        "id", "编号", "序号", "编码", "no", "code", "订单号", "工号",
        "学号", "用户id", "客户id", "product_id", "order_id", "user_id",
    ],
    "name": [
        "名称", "姓名", "名字", "name", "username", "客户名", "产品名",
        "商品名", "公司名", "员工名", "title",
    ],
    "percentage": [
        "百分比", "占比", "比率", "percent", "pct", "%", "完成率",
        "转化率", "通过率", "合格率",
    ],
}

# 单位关键词
UNIT_KEYWORDS = {
    "元": ["金额", "价格", "收入", "支出", "成本", "费用", "销售额", "工资", "薪资", "amount", "price", "revenue", "cost"],
    "个": ["数量", "个数", "件数", "qty", "quantity", "库存", "销量"],
    "人": ["人数", "员工数", "客户数", "用户数"],
    "次": ["次数", "点击", "浏览", "访问"],
    "%": ["率", "比", "百分比", "占比", "percent", "rate", "ratio"],
    "天": ["天数", "时长"],
}


class DataAnalyzer:
    """
    数据理解器

    用法:
        analyzer = DataAnalyzer()

        # 分析文件，返回 DataSchema
        schema = analyzer.analyze_schema("销售数据.xlsx")
        print(schema.to_json())

        # 保存 Schema
        schema.save("schema.json")

        # 兼容旧接口
        profile = analyzer.analyze("data.xlsx")
    """

    DATE_PATTERNS = [
        r"\d{4}[-/]\d{1,2}[-/]\d{1,2}",
        r"\d{1,2}[-/]\d{1,2}[-/]\d{4}",
        r"\d{4}年\d{1,2}月\d{1,2}日",
    ]

    def __init__(self):
        self.service = ExcelService()

    # ==========================================
    # 核心接口：生成 DataSchema
    # ==========================================

    def analyze_schema(self, file_path: str) -> DataSchema:
        """
        分析 Excel 文件，生成完整 DataSchema

        Returns:
            DataSchema 包含所有表、字段类型、语义类型、数据关系
        """
        if not Path(file_path).exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        schema = DataSchema(
            file_path=file_path,
            file_name=Path(file_path).name,
        )

        try:
            xls = pd.ExcelFile(file_path)
            schema.total_sheets = len(xls.sheet_names)

            # 先收集所有表的列信息，用于关系发现
            all_sheets_data = {}

            for sheet_name in xls.sheet_names:
                df = xls.parse(sheet_name)
                sheet_schema = self._analyze_sheet_schema(df, sheet_name)
                schema.sheets.append(sheet_schema)
                schema.total_rows += sheet_schema.row_count
                all_sheets_data[sheet_name] = df

            # 发现表间关系
            schema.relations = self._discover_relations(all_sheets_data)

        except Exception:
            # pandas 失败时用 openpyxl 降级
            schema = self._analyze_with_openpyxl(file_path)

        schema.summary = self._generate_schema_summary(schema)
        return schema

    def analyze(self, file_path: str) -> DataProfile:
        """兼容旧接口：返回 DataProfile"""
        if not Path(file_path).exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        profile = DataProfile(file_path=file_path)

        try:
            xls = pd.ExcelFile(file_path)
            profile.total_sheets = len(xls.sheet_names)

            for sheet_name in xls.sheet_names:
                df = xls.parse(sheet_name)
                sheet_info = self._analyze_sheet_to_info(df, sheet_name)
                profile.sheets.append(sheet_info)
                profile.total_rows += sheet_info.row_count

        except Exception:
            profile = self._analyze_profile_with_openpyxl(file_path)

        profile.summary = self._generate_summary(profile)
        return profile

    def analyze_dataframe(self, df: pd.DataFrame,
                          sheet_name: str = "Sheet1") -> DataSchema:
        """分析单个 DataFrame"""
        schema = DataSchema(file_name="dataframe")
        sheet_schema = self._analyze_sheet_schema(df, sheet_name)
        schema.sheets.append(sheet_schema)
        schema.total_rows = sheet_schema.row_count
        schema.total_sheets = 1
        schema.summary = self._generate_schema_summary(schema)
        return schema

    # ==========================================
    # 表分析
    # ==========================================

    def _analyze_sheet_schema(self, df: pd.DataFrame,
                               sheet_name: str) -> SheetSchema:
        """分析单个 DataFrame → SheetSchema"""
        sheet = SheetSchema(
            name=sheet_name,
            row_count=len(df),
            col_count=len(df.columns),
            has_header=True,
        )

        for col_idx, col_name in enumerate(df.columns):
            col_info = self._analyze_column(df[col_name], str(col_name), col_idx, len(df))
            sheet.columns.append(col_info)

        # 识别主键
        sheet.primary_key = self._detect_primary_key(sheet)
        for col in sheet.columns:
            if col.name == sheet.primary_key:
                col.is_primary_key = True

        # 生成表描述
        sheet.description = self._describe_sheet(sheet)

        return sheet

    def _analyze_column(self, series: pd.Series, col_name: str,
                        col_idx: int, total_rows: int) -> ColumnInfo:
        """分析单列"""
        col = ColumnInfo(name=col_name, index=col_idx)

        # 基础统计
        col.null_count = int(series.isna().sum())
        col.unique_count = int(series.nunique())
        col.null_ratio = col.null_count / max(total_rows, 1)
        col.unique_ratio = col.unique_count / max(total_rows, 1)

        # 推断数据类型
        col.data_type = self._infer_data_type(series, col_name)

        # 推断语义类型
        col.semantic_type = self._infer_semantic_type(
            series, col_name, col.data_type, col.unique_ratio
        )

        # 推断单位
        col.unit = self._infer_unit(col_name, col.semantic_type)

        # 数值统计
        if col.data_type == "number":
            numeric = self._to_numeric_series(series)
            if not numeric.isna().all():
                col.min_value = float(numeric.min())
                col.max_value = float(numeric.max())
                col.avg_value = float(numeric.mean())
                col.sum_value = float(numeric.sum())

        # 日期统计
        elif col.data_type == "date":
            try:
                dates = pd.to_datetime(series, errors="coerce")
                if not dates.isna().all():
                    col.min_value = str(dates.min().date())
                    col.max_value = str(dates.max().date())
            except Exception:
                pass

        # 样本值
        non_null = series.dropna()
        sample = non_null.head(5).tolist()
        col.sample_values = [str(v) for v in sample]
        unique = non_null.drop_duplicates().head(20).tolist()
        col.unique_values = [v.item() if hasattr(v, "item") else v for v in unique]

        # 生成描述（在统计之后）
        col.description = self._describe_column(col)

        return col

    # ==========================================
    # 类型推断
    # ==========================================

    @staticmethod
    def _to_numeric_series(series: pd.Series) -> pd.Series:
        """转换数值列；带百分号的字符串按实际比例值换算。"""
        def convert(value):
            if pd.isna(value) or isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                return value
            text = str(value).replace(",", "").strip()
            is_percent = text.endswith("%")
            if is_percent:
                text = text[:-1].strip()
            try:
                number = float(text)
            except (TypeError, ValueError):
                return None
            return number / 100.0 if is_percent else number

        return pd.to_numeric(series.map(convert), errors="coerce")

    def _infer_data_type(self, series: pd.Series, col_name: str = "") -> str:
        """推断基础数据类型"""
        dtype = str(series.dtype)

        if "datetime" in dtype:
            return "date"
        if "float" in dtype or "int" in dtype:
            return "number"
        if "bool" in dtype:
            return "boolean"

        if dtype == "object":
            non_null = series.dropna()
            if len(non_null) == 0:
                return "text"

            # 尝试数值
            numeric = self._to_numeric_series(non_null)
            if numeric.notna().sum() / len(non_null) > 0.8:
                return "number"

            # 尝试日期
            sample = non_null.head(20).astype(str)
            date_count = sum(
                1 for v in sample
                if any(re.search(p, str(v)) for p in self.DATE_PATTERNS)
            )
            if date_count / max(len(sample), 1) > 0.6:
                return "date"

        return "text"

    def _infer_semantic_type(self, series: pd.Series, col_name: str,
                              data_type: str, unique_ratio: float) -> str:
        """
        推断语义类型：date/amount/quantity/category/metric/id/name/percentage/text
        """
        name_lower = col_name.lower().strip()

        # 1. 日期类型
        if data_type == "date":
            return "date"

        # 2. 关键词匹配（优先级从高到低）
        for sem_type, keywords in SEMANTIC_KEYWORDS.items():
            for kw in keywords:
                if kw in name_lower or kw in col_name:
                    # 百分比优先于 metric
                    if sem_type == "metric" and data_type == "number":
                        if any(p in name_lower for p in ["%", "percent", "占比", "率", "比"]):
                            return "percentage"
                    return sem_type

        # 3. 基于数据特征推断
        if data_type == "number":
            # ID：整数、唯一率高、列名含编号特征
            if unique_ratio > 0.95 and all(
                self._to_numeric_series(series).dropna().apply(
                    lambda x: float(x).is_integer()
                )
            ):
                if any(kw in name_lower for kw in ["id", "no", "code", "编号", "序号"]):
                    return "id"

            # 金额：数值大、通常有小数
            numeric = self._to_numeric_series(series).dropna()
            if len(numeric) > 0:
                avg_val = numeric.mean()
                if avg_val > 100 and any(kw in name_lower for kw in ["额", "价", "金", "费"]):
                    return "amount"
                if avg_val > 1000:
                    return "amount"

            # 数量：整数、数值较小
            if all(numeric.apply(lambda x: float(x).is_integer())) and numeric.mean() < 1000:
                return "quantity"

            # 百分比：值在 0-1 或 0-100 之间
            if numeric.min() >= 0 and numeric.max() <= 1:
                return "percentage"
            if numeric.min() >= 0 and numeric.max() <= 100 and "率" in col_name:
                return "percentage"

            # 默认指标
            return "metric"

        # 4. 文本类型
        if data_type == "text":
            # ID：唯一率高、值短
            if unique_ratio > 0.9 and series.dropna().apply(
                lambda x: len(str(x)) < 20
            ).all():
                if any(kw in name_lower for kw in ["id", "no", "code", "号", "码"]):
                    return "id"

            # 分类：唯一率低（类别少）
            if unique_ratio < 0.5 and series.nunique() < 50:
                return "category"

            # 名称
            if any(kw in name_lower for kw in ["名", "name", "title"]):
                return "name"

            return "text"

        return "unknown"

    def _infer_unit(self, col_name: str, semantic_type: str) -> str:
        """推断单位"""
        for unit, keywords in UNIT_KEYWORDS.items():
            for kw in keywords:
                if kw in col_name:
                    return unit
        if semantic_type == "percentage":
            return "%"
        if semantic_type == "amount":
            return "元"
        if semantic_type == "quantity":
            return "个"
        return ""

    # ==========================================
    # 主键和关系发现
    # ==========================================

    def _detect_primary_key(self, sheet: SheetSchema) -> str:
        """识别主键列"""
        for col in sheet.columns:
            # 主键特征：非空、唯一率高、ID语义
            if col.null_count == 0 and col.unique_ratio > 0.95:
                if col.semantic_type == "id":
                    return col.name
                if col.data_type in ("number", "text") and col.unique_ratio == 1.0:
                    return col.name
        return ""

    def _discover_relations(self, sheets_data: Dict[str, pd.DataFrame]) -> List[DataRelation]:
        """发现表间关系"""
        relations = []

        sheet_names = list(sheets_data.keys())
        for i, s1 in enumerate(sheet_names):
            for j, s2 in enumerate(sheet_names):
                if i >= j:
                    continue
                df1 = sheets_data[s1]
                df2 = sheets_data[s2]

                rel = self._find_relation_between(df1, s1, df2, s2)
                if rel:
                    relations.append(rel)

        return relations

    def _find_relation_between(self, df1: pd.DataFrame, name1: str,
                                df2: pd.DataFrame, name2: str) -> Optional[DataRelation]:
        """查找两表之间的关联"""
        for col1_name in df1.columns:
            col1 = df1[col1_name].dropna().astype(str)
            set1 = set(col1.unique())

            for col2_name in df2.columns:
                col2 = df2[col2_name].dropna().astype(str)
                set2 = set(col2.unique())

                if not set1 or not set2:
                    continue

                # 计算交集比例
                overlap = len(set1 & set2)
                if overlap == 0:
                    continue

                ratio1 = overlap / len(set1)
                ratio2 = overlap / len(set2)

                # 列名相似
                name_match = (str(col1_name).lower() == str(col2_name).lower() or
                             str(col1_name).lower() in str(col2_name).lower() or
                             str(col2_name).lower() in str(col1_name).lower())

                # 高重叠或名称匹配
                if (ratio1 > 0.8 or ratio2 > 0.8) and (name_match or overlap > 5):
                    confidence = max(ratio1, ratio2)
                    if name_match:
                        confidence = min(1.0, confidence + 0.2)

                    # 判断关系类型
                    if ratio1 > 0.9 and ratio2 < 0.9:
                        rel_type = "many_to_one"
                        source, target = name1, name2
                        src_col, tgt_col = col1_name, col2_name
                    elif ratio2 > 0.9 and ratio1 < 0.9:
                        rel_type = "many_to_one"
                        source, target = name2, name1
                        src_col, tgt_col = col2_name, col1_name
                    else:
                        rel_type = "one_to_one"
                        source, target = name1, name2
                        src_col, tgt_col = col1_name, col2_name

                    return DataRelation(
                        source_sheet=source,
                        source_column=str(src_col),
                        target_sheet=target,
                        target_column=str(tgt_col),
                        relation_type=rel_type,
                        confidence=confidence,
                    )

        return None

    # ==========================================
    # 描述生成
    # ==========================================

    def _describe_column(self, col: ColumnInfo) -> str:
        """生成字段描述"""
        sem_labels = {
            "date": "日期字段",
            "amount": "金额字段",
            "quantity": "数量字段",
            "category": "分类字段",
            "metric": "指标字段",
            "id": "标识字段",
            "name": "名称字段",
            "percentage": "百分比字段",
            "text": "文本字段",
            "boolean": "布尔字段",
        }
        label = sem_labels.get(col.semantic_type, "字段")

        parts = [label]
        if col.data_type == "number":
            if col.semantic_type in ("amount", "quantity", "metric"):
                parts.append(f"范围 {col.min_value}~{col.max_value}")
                parts.append(f"平均 {col.avg_value:.1f}")
        elif col.data_type == "date":
            parts.append(f"{col.min_value} ~ {col.max_value}")

        if col.null_count > 0:
            parts.append(f"空值率 {col.null_ratio:.0%}")

        return "，".join(parts)

    def _describe_sheet(self, sheet: SheetSchema) -> str:
        """生成表描述"""
        sem_counts = Counter(c.semantic_type for c in sheet.columns)
        parts = [f"{sheet.name}：{sheet.row_count}行{sheet.col_count}列"]

        if "date" in sem_counts:
            parts.append(f"含{sem_counts['date']}个日期字段")
        if "amount" in sem_counts:
            parts.append(f"{sem_counts['amount']}个金额字段")
        if "category" in sem_counts:
            parts.append(f"{sem_counts['category']}个分类字段")
        if sheet.primary_key:
            parts.append(f"主键={sheet.primary_key}")

        return "，".join(parts)

    def _generate_schema_summary(self, schema: DataSchema) -> str:
        """生成 Schema 摘要"""
        lines = [
            f"文件: {schema.file_name}",
            f"共 {schema.total_sheets} 个工作表，{schema.total_rows} 行数据",
        ]

        for sheet in schema.sheets:
            lines.append(f"\n【{sheet.name}】{sheet.row_count}行 × {sheet.col_count}列")
            if sheet.primary_key:
                lines.append(f"  主键: {sheet.primary_key}")

            for col in sheet.columns:
                sem_icon = {
                    "date": "📅", "amount": "💰", "quantity": "🔢",
                    "category": "🏷️", "metric": "📊", "id": "🔑",
                    "name": "📝", "percentage": "📈",
                }.get(col.semantic_type, "📄")
                unit_str = f" ({col.unit})" if col.unit else ""
                lines.append(f"  {sem_icon} {col.name}: {col.data_type}/{col.semantic_type}{unit_str}")

        if schema.relations:
            lines.append(f"\n数据关系: {len(schema.relations)}条")
            for rel in schema.relations:
                lines.append(f"  {rel.source_sheet}.{rel.source_column} → "
                           f"{rel.target_sheet}.{rel.target_column} "
                           f"({rel.relation_type}, {rel.confidence:.0%})")

        return "\n".join(lines)

    # ==========================================
    # 兼容旧接口
    # ==========================================

    def _analyze_sheet_to_info(self, df: pd.DataFrame, sheet_name: str) -> SheetInfo:
        """旧接口：DataFrame → SheetInfo"""
        info = SheetInfo(
            name=sheet_name,
            row_count=len(df),
            col_count=len(df.columns),
            has_header=True,
        )
        for col_idx, col_name in enumerate(df.columns):
            col_info = self._analyze_column(df[col_name], str(col_name), col_idx, len(df))
            info.columns.append(col_info)
        return info

    def _analyze_profile_with_openpyxl(self, file_path: str) -> DataProfile:
        """openpyxl 降级分析（旧接口）"""
        profile = DataProfile(file_path=file_path)
        wb = self.service.open(file_path).wb

        for ws in wb.worksheets:
            info = SheetInfo(
                name=ws.title,
                row_count=max(0, ws.max_row - 1),
                col_count=ws.max_column,
            )
            headers = []
            for col in range(1, ws.max_column + 1):
                val = ws.cell(row=1, column=col).value
                headers.append(str(val) if val else f"列{col}")

            for col_idx, header in enumerate(headers):
                col_info = ColumnInfo(name=header, index=col_idx)
                values = []
                for row in range(2, min(ws.max_row + 1, 200)):
                    v = ws.cell(row=row, column=col_idx + 1).value
                    if v is not None:
                        values.append(v)

                col_info.null_count = ws.max_row - 1 - len(values)
                col_info.unique_count = len(set(str(v) for v in values))
                col_info.sample_values = [str(v) for v in values[:5]]
                col_info.unique_values = list(dict.fromkeys(values))[:20]

                numeric_count = sum(1 for v in values if isinstance(v, (int, float)))
                if numeric_count > len(values) * 0.8:
                    col_info.data_type = "number"
                    nums = [v for v in values if isinstance(v, (int, float))]
                    if nums:
                        col_info.min_value = min(nums)
                        col_info.max_value = max(nums)
                        col_info.avg_value = sum(nums) / len(nums)
                        col_info.sum_value = sum(nums)
                    col_info.semantic_type = self._infer_semantic_type(
                        pd.Series(values), header, "number",
                        col_info.unique_count / max(len(values), 1)
                    )
                elif any(isinstance(v, datetime) for v in values):
                    col_info.data_type = "date"
                    col_info.semantic_type = "date"
                else:
                    col_info.data_type = "text"
                    col_info.semantic_type = self._infer_semantic_type(
                        pd.Series([str(v) for v in values]), header, "text",
                        col_info.unique_count / max(len(values), 1)
                    )

                info.columns.append(col_info)

            profile.sheets.append(info)
            profile.total_rows += info.row_count

        profile.total_sheets = len(profile.sheets)
        return profile

    def _analyze_with_openpyxl(self, file_path: str) -> DataSchema:
        """openpyxl 降级分析（Schema）"""
        schema = DataSchema(file_path=file_path, file_name=Path(file_path).name)
        wb = self.service.open(file_path).wb

        for ws in wb.worksheets:
            sheet = SheetSchema(
                name=ws.title,
                row_count=max(0, ws.max_row - 1),
                col_count=ws.max_column,
            )

            for col in range(1, ws.max_column + 1):
                header_val = ws.cell(row=1, column=col).value
                col_name = str(header_val) if header_val else f"列{col}"

                values = []
                for row in range(2, min(ws.max_row + 1, 200)):
                    v = ws.cell(row=row, column=col).value
                    if v is not None:
                        values.append(v)

                col_info = ColumnInfo(name=col_name, index=col - 1)
                col_info.null_count = ws.max_row - 1 - len(values)
                col_info.unique_count = len(set(str(v) for v in values))
                col_info.sample_values = [str(v) for v in values[:5]]
                col_info.unique_values = list(dict.fromkeys(values))[:20]

                numeric_count = sum(1 for v in values if isinstance(v, (int, float)))
                if numeric_count > len(values) * 0.8:
                    col_info.data_type = "number"
                    nums = [v for v in values if isinstance(v, (int, float))]
                    if nums:
                        col_info.min_value = min(nums)
                        col_info.max_value = max(nums)
                        col_info.avg_value = sum(nums) / len(nums)
                        col_info.sum_value = sum(nums)
                elif any(isinstance(v, datetime) for v in values):
                    col_info.data_type = "date"
                else:
                    col_info.data_type = "text"

                col_info.semantic_type = self._infer_semantic_type(
                    pd.Series(values), col_name, col_info.data_type,
                    col_info.unique_count / max(len(values), 1)
                )
                col_info.unit = self._infer_unit(col_name, col_info.semantic_type)
                col_info.description = self._describe_column(col_info)
                sheet.columns.append(col_info)

            sheet.primary_key = self._detect_primary_key(sheet)
            schema.sheets.append(sheet)
            schema.total_rows += sheet.row_count

        schema.total_sheets = len(schema.sheets)
        return schema

    def _generate_summary(self, profile: DataProfile) -> str:
        """旧接口摘要"""
        lines = [f"共 {profile.total_sheets} 个工作表，{profile.total_rows} 行数据"]
        for sheet in profile.sheets:
            lines.append(f"\n【{sheet.name}】{sheet.row_count}行 × {sheet.col_count}列")
            for col in sheet.columns[:5]:
                lines.append(f"  {col.name}: {col.data_type}/{col.semantic_type}")
        return "\n".join(lines)

    # ==========================================
    # 便捷方法
    # ==========================================

    def get_numeric_columns(self, profile, sheet_name=None):
        sheet = profile.get_sheet(sheet_name) if hasattr(profile, 'get_sheet') else None
        return [c for c in sheet.columns if c.data_type == "number"] if sheet else []

    def get_date_columns(self, profile, sheet_name=None):
        sheet = profile.get_sheet(sheet_name) if hasattr(profile, 'get_sheet') else None
        return [c for c in sheet.columns if c.data_type == "date"] if sheet else []

    def suggest_analysis(self, profile, sheet_name=None):
        sheet = profile.get_sheet(sheet_name) if hasattr(profile, 'get_sheet') else None
        if not sheet:
            return []
        suggestions = []
        num_cols = [c for c in sheet.columns if c.data_type == "number"]
        date_cols = [c for c in sheet.columns if c.data_type == "date"]

        if num_cols:
            suggestions.append(f"对 {len(num_cols)} 个数值列进行汇总统计")
        if date_cols and num_cols:
            suggestions.append("按日期维度生成趋势分析")
        return suggestions


def analyze_excel(file_path: str) -> DataProfile:
    """便捷函数：分析 Excel（旧接口）"""
    return DataAnalyzer().analyze(file_path)


def analyze_schema(file_path: str) -> DataSchema:
    """便捷函数：生成 DataSchema"""
    return DataAnalyzer().analyze_schema(file_path)
