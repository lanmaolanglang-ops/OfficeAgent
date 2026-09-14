"""
Format Rule Parser - 自然语言排版规则解析器
将用户的自然语言排版要求转换为结构化配置

示例:
    输入: "正文小四宋体，标题黑体小三，1.25倍行距，首行缩进2字符"
    输出: {
        "body": {"font_cn": "宋体", "size": "小四", "line_spacing": 1.25, ...},
        "headings": {"1": {"font": "黑体", "size": "小三"}, ...}
    }
"""
import re
from dataclasses import dataclass, field
from typing import Optional


# ============== 字体名映射 ==============

KNOWN_FONTS = {
    # 中文字体
    "宋体", "黑体", "仿宋", "楷体", "微软雅黑", "微软正黑体",
    "仿宋_GB2312", "楷体_GB2312", "方正小标宋简体", "方正书宋",
    "思源黑体", "思源宋体", "苹方", "华文宋体", "华文黑体",
    "华文楷体", "华文仿宋", "隶书", "幼圆", "华文中宋",
    # 英文字体
    "Times New Roman", "Arial", "Calibri", "Helvetica",
    "Georgia", "Verdana", "Courier New", "Cambria",
}

# 常见字体别名
FONT_ALIASES = {
    "times": "Times New Roman",
    "tnr": "Times New Roman",
    "雅黑": "微软雅黑",
    "msyh": "微软雅黑",
    "小标宋": "方正小标宋简体",
    "书宋": "方正书宋",
}


# ============== 中文字号映射 ==============

CHINESE_SIZE_MAP = {
    "初号": 42, "小初": 36,
    "一号": 26, "小一": 24,
    "二号": 22, "小二": 18,
    "三号": 16, "小三": 15,
    "四号": 14, "小四": 12,
    "五号": 10.5, "小五": 9,
    "六号": 7.5, "小六": 6.5,
    "七号": 5.5, "八号": 5,
}

SIZE_PATTERN = re.compile(
    r"(初号|小初|一号|小一|二号|小二|三号|小三|四号|小四|五号|小五|六号|小六|七号|八号)"
)

# 磅值模式: 12pt, 12磅, 12号
PT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:pt|磅|p)", re.IGNORECASE)


# ============== 颜色映射 ==============

COLOR_MAP = {
    "黑色": "000000", "黑": "000000",
    "白色": "FFFFFF", "白": "FFFFFF",
    "红色": "FF0000", "红": "FF0000",
    "蓝色": "0000FF", "蓝": "0000FF",
    "绿色": "008000", "绿": "008000",
    "灰色": "808080", "灰": "808080",
    "黄色": "FFFF00", "黄": "FFFF00",
    "橙色": "FFA500", "橙": "FFA500",
    "紫色": "800080", "紫": "800080",
    "深蓝": "1F4E79", "浅蓝": "ADD8E6",
    "深红": "8B0000", "暗红": "8B0000",
}

HEX_COLOR_PATTERN = re.compile(r"#([0-9A-Fa-f]{6})")


# ============== 行距映射 ==============

LINE_SPACING_MAP = {
    "单倍": 1.0, "单倍行距": 1.0,
    "1.0倍": 1.0, "1倍": 1.0,
    "1.15倍": 1.15,
    "1.25倍": 1.25,
    "1.5倍": 1.5, "1.5倍行距": 1.5,
    "双倍": 2.0, "双倍行距": 2.0, "2倍": 2.0,
    "固定值": None,  # 需配合磅值
}

LINE_SPACING_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*倍(?:行距)?", re.IGNORECASE
)
PT_SPACING_PATTERN = re.compile(
    r"(?:固定值|固定)\s*(\d+(?:\.\d+)?)\s*(?:pt|磅|p)?", re.IGNORECASE
)


# ============== 对齐方式映射 ==============

ALIGNMENT_MAP = {
    "左对齐": "left",
    "右对齐": "right",
    "居中": "center", "居中对齐": "center",
    "两端对齐": "justify", "分散对齐": "distribute",
}


# ============== 标题层级关键词 ==============

HEADING_LEVEL_MAP = {
    "一级标题": 1, "一级": 1, "大标题": 1, "1级标题": 1,
    "二级标题": 2, "二级": 2, "2级标题": 2,
    "三级标题": 3, "三级": 3, "3级标题": 3,
    "四级标题": 4, "四级": 4, "4级标题": 4,
    "标题": None,  # 通用标题，需推断
}


# ============== 缩进模式 ==============

INDENT_CHAR_PATTERN = re.compile(r"首行缩进\s*(\d+(?:\.\d+)?)\s*字符")
INDENT_PT_PATTERN = re.compile(r"首行缩进\s*(\d+(?:\.\d+)?)\s*(?:pt|磅|p)")
INDENT_CM_PATTERN = re.compile(r"首行缩进\s*(\d+(?:\.\d+)?)\s*(?:cm|厘米)")
HANGING_PATTERN = re.compile(r"悬挂缩进\s*(\d+(?:\.\d+)?)\s*(字符|pt|磅|cm|厘米)?")


# ============== 段前段后模式 ==============

SPACE_BEFORE_PATTERN = re.compile(
    r"段前\s*(\d+(?:\.\d+)?)\s*(pt|磅|行|字符)?", re.IGNORECASE
)
SPACE_AFTER_PATTERN = re.compile(
    r"段后\s*(\d+(?:\.\d+)?)\s*(pt|磅|行|字符)?", re.IGNORECASE
)


@dataclass
class ParsedFormatRule:
    """解析后的格式规则"""
    # 正文配置
    body: dict = field(default_factory=dict)
    # 标题配置 {1: {...}, 2: {...}, ...}
    headings: dict[int, dict] = field(default_factory=dict)
    # 表格配置
    table: dict = field(default_factory=dict)
    # 页面配置
    page: dict = field(default_factory=dict)
    # 原始文本
    raw_text: str = ""
    # 解析到的关键词
    matched_keywords: list[str] = field(default_factory=list)

    def to_config_dict(self) -> dict:
        """
        转换为 WordService.config_from_dict 可用的字典格式
        """
        result = {}

        # 正文
        if self.body:
            if "font_cn" in self.body:
                result["font"] = self.body["font_cn"]
            if "font_en" in self.body:
                result["en_font"] = self.body["font_en"]
            if "size" in self.body:
                result["size"] = self.body["size"]
            if "line_spacing" in self.body:
                result["line_spacing"] = self.body["line_spacing"]
            if "line_spacing_rule" in self.body:
                result["line_spacing_rule"] = self.body["line_spacing_rule"]
            if "alignment" in self.body:
                result["alignment"] = self.body["alignment"]
            if "first_line_indent" in self.body:
                result["first_line_indent"] = self.body["first_line_indent"]
            if "first_line_indent_chars" in self.body:
                result["first_line_indent_chars"] = self.body["first_line_indent_chars"]
            if "bold" in self.body:
                result["bold"] = self.body["bold"]
            if "italic" in self.body:
                result["italic"] = self.body["italic"]
            if "color" in self.body:
                result["color"] = self.body["color"]
            if "space_before" in self.body:
                result["space_before"] = self.body["space_before"]
            if "space_after" in self.body:
                result["space_after"] = self.body["space_after"]

        # 标题
        if self.headings:
            result["headings"] = {}
            for level, h_cfg in self.headings.items():
                h = {}
                if "font_cn" in h_cfg:
                    h["font"] = h_cfg["font_cn"]
                if "size" in h_cfg:
                    h["size"] = h_cfg["size"]
                if "bold" in h_cfg:
                    h["bold"] = h_cfg["bold"]
                if "alignment" in h_cfg:
                    h["alignment"] = h_cfg["alignment"]
                if "color" in h_cfg:
                    h["color"] = h_cfg["color"]
                h["numbering"] = h_cfg.get("numbering", True)
                result["headings"][str(level)] = h

        # 表格
        if self.table:
            if "three_line" in self.table:
                result["table_three_line"] = bool(self.table["three_line"])
            if "top_border" in self.table:
                result["table_top"] = self.table["top_border"]
            if "numbering" in self.table:
                result["table_numbering"] = self.table["numbering"]

        return result


class FormatRuleParser:
    """
    自然语言格式规则解析器

    用法:
        parser = FormatRuleParser()
        rule = parser.parse("正文小四宋体，标题黑体小三，1.25倍行距")
        config_dict = rule.to_config_dict()
    """

    def __init__(self):
        self.keywords_found: list[str] = []

    def parse(self, text: str) -> ParsedFormatRule:
        """
        解析自然语言排版规则

        Args:
            text: 用户的自然语言描述，如
                  "正文小四宋体，标题黑体小三，1.25倍行距，首行缩进2字符"

        Returns:
            ParsedFormatRule 对象
        """
        self.keywords_found = []
        rule = ParsedFormatRule(raw_text=text)

        # 预处理：统一标点，去除多余空格
        normalized = self._normalize(text)

        # 分段处理（按逗号、句号、分号分割）
        segments = re.split(r"[，,。；;、\s]+", normalized)
        segments = [s.strip() for s in segments if s.strip()]

        # 先提取全局设置（不区分正文/标题的）
        global_config: dict = {}
        self._parse_global_settings(normalized, global_config, rule)

        # 逐段解析，判断作用域
        for segment in segments:
            self._parse_segment(segment, rule, global_config)

        # 如果标题没有单独设置，用全局标题设置
        self._apply_heading_defaults(rule, global_config)

        # 如果正文没有单独设置，用全局设置
        self._apply_body_defaults(rule, global_config)

        rule.matched_keywords = self.keywords_found
        return rule

    def _normalize(self, text: str) -> str:
        """标准化文本"""
        # 全角转半角（部分）
        text = text.replace("：", ":").replace("．", ".")
        text = text.replace("（", "(").replace("）", ")")
        return text

    def _parse_global_settings(self, text: str, global_cfg: dict,
                                rule: ParsedFormatRule):
        """解析全局设置（不区分正文/标题的）"""

        # 行距（全局）
        spacing = self._extract_line_spacing(text)
        if spacing is not None:
            global_cfg["line_spacing"] = spacing
            if PT_SPACING_PATTERN.search(text):
                global_cfg["line_spacing_rule"] = "exactly"
            self.keywords_found.append(f"行距:{spacing}")

        # 对齐方式（全局）
        alignment = self._extract_alignment(text)
        if alignment:
            global_cfg["alignment"] = alignment
            self.keywords_found.append(f"对齐:{alignment}")

        # 首行缩进（全局，通常用于正文）
        indent_chars_match = INDENT_CHAR_PATTERN.search(text)
        if indent_chars_match:
            indent_chars = float(indent_chars_match.group(1))
            global_cfg["first_line_indent_chars"] = indent_chars
            self.keywords_found.append(f"首行缩进:{indent_chars}字符")
        else:
            indent = self._extract_first_line_indent(text)
            if indent is not None:
                global_cfg["first_line_indent"] = indent
                self.keywords_found.append(f"首行缩进:{indent}pt")

        # 段前段后（全局）
        sb = self._extract_space_before(text)
        if sb is not None:
            global_cfg["space_before"] = sb
            self.keywords_found.append(f"段前:{sb}pt")

        sa = self._extract_space_after(text)
        if sa is not None:
            global_cfg["space_after"] = sa
            self.keywords_found.append(f"段后:{sa}pt")

        # 颜色（全局）
        color = self._extract_color(text)
        if color:
            global_cfg["color"] = color
            self.keywords_found.append(f"颜色:{color}")

        # 加粗/斜体：不作为全局设置，在片段级别处理作用域
        # （避免"标题加粗"导致正文也加粗）
        # 但记录是否出现了加粗关键词
        self._has_bold = bool(re.search(r"加粗|粗体", text))
        self._has_italic = bool(re.search(r"斜体", text))

        # 三线表
        if re.search(r"三线表|三线格", text):
            rule.table["three_line"] = True
            self.keywords_found.append("三线表")

    def _parse_segment(self, segment: str, rule: ParsedFormatRule,
                       global_cfg: dict):
        """解析单个片段，判断作用于正文还是标题"""

        # 判断是否指定了标题层级
        heading_level = self._detect_heading_level(segment)
        is_body = self._is_body_segment(segment)

        # 提取字体
        font = self._extract_font(segment)
        # 提取字号
        size = self._extract_size(segment)

        if heading_level is not None:
            # 标题配置
            # 片段级加粗/斜体检测
            seg_bold = bool(re.search(r"加粗|粗体", segment))
            seg_italic = bool(re.search(r"斜体", segment))
            seg_no_number = bool(
                re.search(r"不编号|取消编号|无编号|去掉编号|不要编号", segment)
            )

            if heading_level == 0:
                # 通用"标题"，应用到所有级别
                for level in range(1, 5):
                    if level not in rule.headings:
                        rule.headings[level] = {}
                    if font:
                        rule.headings[level]["font_cn"] = font
                    if size:
                        rule.headings[level]["size"] = size
                    if seg_bold:
                        rule.headings[level]["bold"] = True
                    if seg_italic:
                        rule.headings[level]["italic"] = True
                    if seg_no_number:
                        rule.headings[level]["numbering"] = False
                    if "alignment" in global_cfg:
                        rule.headings[level]["alignment"] = global_cfg["alignment"]
                    if "color" in global_cfg:
                        rule.headings[level]["color"] = global_cfg["color"]
                if font:
                    self.keywords_found.append(f"标题字体:{font}")
                if size:
                    self.keywords_found.append(f"标题字号:{size}")
                if seg_bold:
                    self.keywords_found.append("标题加粗")
            else:
                # 指定级别
                if heading_level not in rule.headings:
                    rule.headings[heading_level] = {}
                if font:
                    rule.headings[heading_level]["font_cn"] = font
                if size:
                    rule.headings[heading_level]["size"] = size
                # 标题默认加粗
                rule.headings[heading_level]["bold"] = True
                if seg_bold:
                    rule.headings[heading_level]["bold"] = True
                if seg_italic:
                    rule.headings[heading_level]["italic"] = True
                if seg_no_number:
                    rule.headings[heading_level]["numbering"] = False
                if "alignment" in global_cfg:
                    rule.headings[heading_level]["alignment"] = global_cfg["alignment"]
                if "color" in global_cfg:
                    rule.headings[heading_level]["color"] = global_cfg["color"]

        elif is_body or font or size:
            # 正文配置
            seg_bold = bool(re.search(r"加粗|粗体", segment))
            seg_italic = bool(re.search(r"斜体", segment))

            if font:
                rule.body["font_cn"] = font
                self.keywords_found.append(f"正文字体:{font}")
            if size:
                rule.body["size"] = size
                self.keywords_found.append(f"正文字号:{size}")
            if seg_bold:
                rule.body["bold"] = True
                self.keywords_found.append("正文加粗")
            if seg_italic:
                rule.body["italic"] = True

    def _detect_heading_level(self, segment: str) -> Optional[int]:
        """
        检测片段中的标题层级
        返回: 1-4 表示具体级别，0 表示通用"标题"，None 表示不是标题
        """
        for keyword, level in HEADING_LEVEL_MAP.items():
            if keyword in segment:
                return level if level is not None else 0
        return None

    def _is_body_segment(self, segment: str) -> bool:
        """判断片段是否明确指向正文"""
        body_keywords = ["正文", "内容", "文本", "段落"]
        return any(kw in segment for kw in body_keywords)

    def _apply_heading_defaults(self, rule: ParsedFormatRule, global_cfg: dict):
        """对没有显式设置的标题级别应用默认值"""
        # 如果用户说了"标题黑体小三"但没说具体级别，已经在_parse_segment处理了
        # 这里确保标题默认加粗
        for level in rule.headings:
            if "bold" not in rule.headings[level]:
                rule.headings[level]["bold"] = True
            rule.headings[level].setdefault("numbering", True)

    def _apply_body_defaults(self, rule: ParsedFormatRule, global_cfg: dict):
        """将全局设置应用到正文（加粗/斜体不从全局继承，避免标题加粗影响正文）"""
        for key in ["line_spacing", "line_spacing_rule", "alignment", "first_line_indent",
                    "first_line_indent_chars",
                    "space_before", "space_after", "color"]:
            if key in global_cfg and key not in rule.body:
                rule.body[key] = global_cfg[key]

    # ============== 提取器 ==============

    def _extract_font(self, text: str) -> Optional[str]:
        """提取字体名"""
        # 先检查已知字体（长名优先）
        for font in sorted(KNOWN_FONTS, key=len, reverse=True):
            if font in text:
                return font

        # 检查别名
        for alias, font in FONT_ALIASES.items():
            if alias in text:
                return font

        # 尝试匹配"XX体"模式
        m = re.search(r"([\u4e00-\u9fff]{2,4})体", text)
        if m:
            candidate = m.group(0)
            if candidate in KNOWN_FONTS:
                return candidate
            # 常见组合
            if candidate in ["宋体", "黑体", "楷体", "仿宋", "隶书", "幼圆"]:
                return candidate

        return None

    def _extract_size(self, text: str) -> str | float | None:
        """提取字号（返回中文字号名或磅值）"""
        # 先移除"段前X磅"/"段后X磅"，避免误识别为字号
        text_clean = SPACE_BEFORE_PATTERN.sub("", text)
        text_clean = SPACE_AFTER_PATTERN.sub("", text_clean)
        # 移除"固定值X磅"
        text_clean = PT_SPACING_PATTERN.sub("", text_clean)

        # 中文字号
        m = SIZE_PATTERN.search(text_clean)
        if m:
            return m.group(1)

        # 磅值
        m = PT_PATTERN.search(text_clean)
        if m:
            return float(m.group(1))

        # 纯数字+号（如"12号"）
        m = re.search(r"(\d+(?:\.\d+)?)\s*号", text_clean)
        if m:
            val = float(m.group(1))
            # 反查中文字号
            for cn_name, pt_val in CHINESE_SIZE_MAP.items():
                if abs(pt_val - val) < 0.1:
                    return cn_name
            return val

        return None

    def _extract_color(self, text: str) -> Optional[str]:
        """提取颜色（排除字体名误匹配）"""
        # 先提取字体名，避免"黑体"匹配"黑"
        font = self._extract_font(text)
        text_for_color = text
        if font:
            text_for_color = text.replace(font, "")

        # 十六进制
        m = HEX_COLOR_PATTERN.search(text_for_color)
        if m:
            return m.group(1).upper()

        # 中文颜色名（精确匹配，"黑体"不算"黑色"）
        # 使用正则确保颜色词后面不是"体"字
        # 优先选择文本中起点更早、同起点名称更长的颜色。“深蓝色”里
        # “深蓝”起点早于“蓝色”，不能被基础色抢先。
        matches = []
        for name, hex_val in COLOR_MAP.items():
            pattern = re.escape(name) + r"(?!体)"
            match = re.search(pattern, text_for_color)
            if match:
                matches.append((match.start(), -len(name), hex_val))
        if matches:
            return min(matches)[2]

        return None

    def _extract_line_spacing(self, text: str) -> Optional[float]:
        """提取行距"""
        # 具体倍数
        m = LINE_SPACING_PATTERN.search(text)
        if m:
            return float(m.group(1))

        # 固定值
        m = PT_SPACING_PATTERN.search(text)
        if m:
            return float(m.group(1))  # 固定值磅数

        # 命名行距
        for name, val in LINE_SPACING_MAP.items():
            if name in text and val is not None:
                return val

        return None

    def _extract_alignment(self, text: str) -> Optional[str]:
        """提取对齐方式"""
        for keyword, align in ALIGNMENT_MAP.items():
            if keyword in text:
                return align
        return None

    def _extract_first_line_indent(self, text: str) -> Optional[float]:
        """提取首行缩进的绝对磅值；字符值由解析主流程保留原单位。"""
        # X磅
        m = INDENT_PT_PATTERN.search(text)
        if m:
            return float(m.group(1))

        # X厘米
        m = INDENT_CM_PATTERN.search(text)
        if m:
            cm = float(m.group(1))
            return cm * 28.35  # 1cm ≈ 28.35pt

        return None

    DEFAULT_LINE_HEIGHT_PT = 12.0

    def _line_height_pt(self, text: str) -> float:
        """“行”换算为磅（P3-98）：单倍行距约为字号的 1.3 倍。

        规则文本声明了字号时按字号推算；没有字号信息时回退 12pt，
        不再无视字号恒定乘 12。
        """
        size = self._extract_size(text)
        pt: Optional[float] = None
        if isinstance(size, (int, float)):
            pt = float(size)
        elif isinstance(size, str) and size in CHINESE_SIZE_MAP:
            pt = float(CHINESE_SIZE_MAP[size])
        return pt * 1.3 if pt else self.DEFAULT_LINE_HEIGHT_PT

    def _extract_space_before(self, text: str) -> Optional[float]:
        """提取段前距（返回磅值）"""
        m = SPACE_BEFORE_PATTERN.search(text)
        if m:
            val = float(m.group(1))
            unit = m.group(2)
            if unit == "行":
                return val * self._line_height_pt(text)
            return val
        return None

    def _extract_space_after(self, text: str) -> Optional[float]:
        """提取段后距（返回磅值）"""
        m = SPACE_AFTER_PATTERN.search(text)
        if m:
            val = float(m.group(1))
            unit = m.group(2)
            if unit == "行":
                return val * self._line_height_pt(text)
            return val
        return None


# ============== 便捷函数 ==============

def parse_format_rule(text: str) -> dict:
    """
    便捷函数：解析自然语言排版规则，直接返回 WordService 可用的配置字典

    Args:
        text: 自然语言描述

    Returns:
        可直接传给 WordService.process() 的 config_dict

    示例:
        >>> config = parse_format_rule("正文小四宋体，1.25倍行距，首行缩进2字符")
        >>> result = word_service.process("file.docx", config_dict=config)
    """
    parser = FormatRuleParser()
    rule = parser.parse(text)
    return rule.to_config_dict()
