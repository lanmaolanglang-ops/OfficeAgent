"""
Template Analyzer - PPT 模板解析器

功能：
1. 解析 .pptx 模板的母版、主题颜色、字体方案
2. 提取每个版式的占位符位置（标题/正文/图片区域）
3. 输出 TemplateConfig，后续生成 PPT 时严格遵循模板布局
4. 支持基于模板母版直接创建新幻灯片

支持提取：
- 母版信息（数量、名称、尺寸）
- 主题颜色（dk1/lt1/dk2/lt2/accent1-6/超链接）
- 主题字体（标题字体/正文字体，含中文/英文）
- 页面尺寸
- 每个版式的占位符（类型、位置、大小）
- 背景色/背景填充
"""
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass, field, asdict
from collections import Counter
import io
import logging
import re

from pptx import Presentation
from pptx.presentation import Presentation as PresentationType
from pptx.oxml.ns import qn
from lxml import etree  # type: ignore[import-untyped]

from .models import ColorScheme, FontScheme, PPTOutline

logger = logging.getLogger("office_agent.ppt_agent.template_analyzer")


def clear_slides(prs: PresentationType) -> None:
    """清空所有幻灯片（保留母版/版式/主题），且不留下孤儿 slide 部件。

    python-pptx 的 ``XmlPart.drop_rel`` 带引用计数守卫：rId 在所属部件
    XML 中出现 2 次及以上时拒绝移除关系。模板若含自定义放映
    （``p:custShowLst``），slide 的 rId 会被其二次引用，朴素习语
    （先 drop_rel 再移除 sldId）会静默失败——保存后 slide 部件仍留在
    package 中却不在 sldIdLst 里（孤儿部件），可能触发 PowerPoint
    修复警告。

    因此顺序必须是：先移除 custShowLst（清空 slide 后自定义放映本身
    已无意义），再移除 sldId 的 XML 引用，最后 drop_rel（此时引用计数
    归零，守卫放行）。slide 独占的图片/图表/嵌入 workbook/备注页会在
    保存时随关系图遍历自动剔除；仍被母版/版式/其它页引用的共享部件
    不受影响。
    """
    pres_element = prs.part._element
    for cust_show_lst in pres_element.findall(qn("p:custShowLst")):
        pres_element.remove(cust_show_lst)
    sld_id_lst = prs.slides._sldIdLst
    for sld_id in list(sld_id_lst):
        rel_id = sld_id.get(qn("r:id"))
        sld_id_lst.remove(sld_id)
        try:
            prs.part.drop_rel(rel_id)
        except KeyError:
            pass

# 命名空间
nsmap = {
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}


# ==========================================
# 数据结构
# ==========================================

@dataclass
class PlaceholderInfo:
    """占位符信息（位置、大小、类型）"""
    idx: int                 # 占位符索引
    ph_type: str             # 类型：title/body/ctrTitle/subTitle/pic/chart/table/dt/ftr/sldNum
    name: str = ""           # 占位符名称
    left: float = 0.0        # 左边距（英寸）
    top: float = 0.0         # 上边距（英寸）
    width: float = 0.0       # 宽度（英寸）
    height: float = 0.0      # 高度（英寸）
    font_size: float = 0.0   # 字体大小（pt，0=未设置）
    font_name: str = ""      # 字体名
    font_bold: bool = False  # 是否加粗
    alignment: str = ""      # 对齐方式

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LayoutInfo:
    """版式信息"""
    index: int                    # 版式索引
    name: str                     # 版式名称
    layout_type: str = "blank"    # 推断的版式类型
    placeholder_count: int = 0
    placeholders: List[PlaceholderInfo] = field(default_factory=list)

    # 便捷属性
    has_title: bool = False
    has_content: bool = False
    has_picture: bool = False
    has_chart: bool = False
    has_table: bool = False
    has_two_content: bool = False

    # 常用位置（英寸）
    title_left: float = 0.0
    title_top: float = 0.0
    title_width: float = 0.0
    title_height: float = 0.0
    content_left: float = 0.0
    content_top: float = 0.0
    content_width: float = 0.0
    content_height: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class MasterInfo:
    """母版信息"""
    index: int
    name: str
    layout_count: int = 0
    layouts: List[LayoutInfo] = field(default_factory=list)
    # 母版级别的占位符位置
    title_placeholder: Optional[PlaceholderInfo] = None
    body_placeholder: Optional[PlaceholderInfo] = None

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "layout_count": self.layout_count,
            "title_pos": self.title_placeholder.to_dict() if self.title_placeholder else None,
            "body_pos": self.body_placeholder.to_dict() if self.body_placeholder else None,
        }


def _is_hex6(value) -> bool:
    """是否为 6 位十六进制颜色（sysClr.val 是主题 token，不是 hex）。"""
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9A-Fa-f]{6}", value.strip()))


def _readable_on_dark(bg_hex: str) -> str:
    """给定背景色，返回在其上可读的前景（深底白字/浅底深字）。"""
    try:
        raw = (bg_hex or "").lstrip("#")
        red, green, blue = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
    except (TypeError, ValueError):
        return "#FFFFFF"
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return "#FFFFFF" if luminance < 128 else "#000000"


@dataclass
class ThemeColorInfo:
    """主题颜色信息"""
    dk1: str = "#000000"        # 文字1（深色）
    lt1: str = "#FFFFFF"        # 背景1（浅色）
    dk2: str = "#1F4E79"        # 文字2（深色）
    lt2: str = "#E7E6E6"        # 背景2（浅色）
    accent1: str = "#4472C4"    # 强调色1
    accent2: str = "#ED7D31"    # 强调色2
    accent3: str = "#A5A5A5"    # 强调色3
    accent4: str = "#FFC000"    # 强调色4
    accent5: str = "#5B9BD5"    # 强调色5
    accent6: str = "#70AD47"    # 强调色6
    hlink: str = "#0563C1"      # 超链接
    folHlink: str = "#954F72"   # 已访问超链接

    def to_dict(self) -> dict:
        return asdict(self)

    def to_color_scheme(self) -> ColorScheme:
        """转换为 ColorScheme"""
        # text_light 用在 bg_dark 之上：按 bg_dark 亮度推导，深色底用白字、
        # 偏浅的“深色”底改用深字，避免白字落在浅底上不可见（P3-45）。
        return ColorScheme(
            primary=self.dk2,
            secondary=self.accent1,
            accent=self.accent2,
            bg=self.lt1,
            bg_dark=self.dk2,
            text=self.dk1,
            text_light=_readable_on_dark(self.dk2),
            text_muted="#666666",
            line="#D9D9D9",
        )


@dataclass
class ThemeFontInfo:
    """主题字体信息"""
    major_latin: str = "Calibri"      # 标题西文字体
    major_ea: str = "微软雅黑"         # 标题中文字体
    minor_latin: str = "Calibri"      # 正文西文字体
    minor_ea: str = "微软雅黑"         # 正文中文字体
    major_size: float = 28.0          # 标题默认字号
    minor_size: float = 18.0          # 正文默认字号

    def to_dict(self) -> dict:
        return asdict(self)

    def to_font_scheme(self) -> FontScheme:
        """转换为 FontScheme"""
        return FontScheme(
            title_cn=self.major_ea,
            title_en=self.major_latin,
            body_cn=self.minor_ea,
            body_en=self.minor_latin,
            title_size=self.major_size,
            body_size=self.minor_size,
        )


@dataclass
class TemplateConfig:
    """
    模板配置 - 完整的模板解析结果

    生成 PPT 时必须遵循此配置
    """
    file_path: str = ""
    name: str = ""

    # 页面尺寸
    slide_width: float = 13.333      # 英寸
    slide_height: float = 7.5        # 英寸

    # 主题
    theme_name: str = "custom"
    colors: ThemeColorInfo = field(default_factory=ThemeColorInfo)
    fonts: ThemeFontInfo = field(default_factory=ThemeFontInfo)

    # 母版和版式
    masters: List[MasterInfo] = field(default_factory=list)
    layouts: List[LayoutInfo] = field(default_factory=list)

    # 背景
    bg_color: str = "#FFFFFF"
    bg_fill_type: str = "solid"      # solid/gradient/picture/none

    # 统计
    slide_count: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "file_path": self.file_path,
            "slide_width": self.slide_width,
            "slide_height": self.slide_height,
            "theme_name": self.theme_name,
            "bg_color": self.bg_color,
            "colors": self.colors.to_dict(),
            "fonts": self.fonts.to_dict(),
            "layouts": [
                {
                    "index": layout.index,
                    "name": layout.name,
                    "layout_type": layout.layout_type,
                    "has_title": layout.has_title,
                    "has_content": layout.has_content,
                    "has_picture": layout.has_picture,
                    "title_pos": {"left": layout.title_left, "top": layout.title_top,
                                  "width": layout.title_width, "height": layout.title_height},
                    "content_pos": {"left": layout.content_left, "top": layout.content_top,
                                    "width": layout.content_width, "height": layout.content_height},
                    "placeholders": [p.to_dict() for p in layout.placeholders],
                }
                for layout in self.layouts
            ],
            "masters": [m.to_dict() for m in self.masters],
            "slide_count": self.slide_count,
        }

    def to_json(self, indent=2, ensure_ascii=False) -> str:
        import json
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=ensure_ascii)

    def get_color_scheme(self) -> ColorScheme:
        return self.colors.to_color_scheme()

    def get_font_scheme(self) -> FontScheme:
        return self.fonts.to_font_scheme()

    def find_layout(self, layout_type: str) -> Optional[LayoutInfo]:
        """根据类型查找版式"""
        for layout in self.layouts:
            if layout.layout_type == layout_type:
                return layout
        return None

    def get_title_position(self, layout_idx: int = 1) -> Tuple[float, float, float, float]:
        """获取指定版式的标题位置 (left, top, width, height)"""
        if (isinstance(layout_idx, int) and not isinstance(layout_idx, bool)
                and 0 <= layout_idx < len(self.layouts)):
            layout = self.layouts[layout_idx]
            if layout.has_title:
                return (layout.title_left, layout.title_top,
                        layout.title_width, layout.title_height)
        # 默认值
        return (0.5, 0.3, 12.333, 1.0)

    def get_content_position(self, layout_idx: int = 1) -> Tuple[float, float, float, float]:
        """获取指定版式的正文位置"""
        if (isinstance(layout_idx, int) and not isinstance(layout_idx, bool)
                and 0 <= layout_idx < len(self.layouts)):
            layout = self.layouts[layout_idx]
            if layout.has_content:
                return (layout.content_left, layout.content_top,
                        layout.content_width, layout.content_height)
        return (0.5, 1.5, 12.333, 5.5)


# ==========================================
# TemplateAnalyzer 主类
# ==========================================

class TemplateAnalyzer:
    """
    PPT 模板解析器

    用法:
        analyzer = TemplateAnalyzer()
        config = analyzer.analyze("template.pptx")

        # 查看配置
        print(config.to_json())

        # 获取颜色和字体
        colors = config.get_color_scheme()
        fonts = config.get_font_scheme()

        # 获取标题位置
        left, top, width, height = config.get_title_position(layout_idx=1)

        # 基于模板创建新 PPT
        output = analyzer.create_from_template(
            "template.pptx", slides_data, "output.pptx"
        )
    """

    # 版式类型映射（根据占位符组合推断）
    LAYOUT_TYPE_MAP = {
        # (has_title, has_content, has_picture, has_two_content, has_chart)
        (True, False, False, False, False): "cover",
        (True, True, False, False, False): "content",
        (True, True, True, False, False): "content_image",
        (True, True, False, True, False): "two_column",
        (True, False, True, False, False): "section_image",
        (False, True, False, False, True): "chart",
        (False, True, False, False, False): "content_only",
    }

    def analyze(self, template_path: str) -> TemplateConfig:
        """
        分析 PPT 模板，提取完整配置

        Args:
            template_path: .pptx 文件路径

        Returns:
            TemplateConfig 完整模板配置
        """
        path = Path(template_path)
        if not path.exists():
            raise FileNotFoundError(f"模板文件不存在: {template_path}")

        with open(str(path), "rb") as _fh:
            prs = Presentation(io.BytesIO(_fh.read()))

        width_emu = prs.slide_width
        height_emu = prs.slide_height
        config = TemplateConfig(
            file_path=str(path),
            name=path.stem,
            slide_width=width_emu / 914400 if width_emu is not None else 13.333,
            slide_height=height_emu / 914400 if height_emu is not None else 7.5,
            slide_count=len(prs.slides),
        )

        # 1. 提取主题颜色和字体（从母版主题 XML）
        self._extract_theme(prs, config)

        # 2. 提取母版信息
        self._extract_masters(prs, config)

        # 3. 提取所有版式和占位符位置
        self._extract_all_layouts(prs, config)

        # 4. 提取背景色
        self._extract_background(prs, config)

        # 5. 从现有幻灯片补充分析（如果有示例页）
        if config.slide_count > 0:
            self._analyze_sample_slides(prs, config)

        return config

    def apply_to_outline(self, template_path: str, outline: PPTOutline) -> PPTOutline:
        """将模板配置应用到 PPTOutline"""
        config = self.analyze(template_path)

        outline.color_scheme = config.get_color_scheme()
        outline.font_scheme = config.get_font_scheme()
        outline.slide_width = config.slide_width
        outline.slide_height = config.slide_height
        outline.theme = "custom"
        setattr(outline, "_template_config", config)  # 附加模板配置供 Service 使用

        return outline

    def create_from_template(self, template_path: str, slides_data: list,
                             output_path: str, overwrite_template: bool = False) -> str:
        """
        基于模板母版创建新 PPT。

        默认拒绝 output 与 template 同路径：不得静默覆盖用户模板（P2-52）。
        """
        tpl = Path(template_path).resolve()
        out = Path(output_path)
        if not overwrite_template:
            try:
                if out.resolve() == tpl:
                    out = out.with_name(f"{out.stem}_from_template{out.suffix}")
            except OSError:
                if str(out) == str(template_path):
                    out = out.with_name(f"{out.stem}_from_template{out.suffix}")
        with open(template_path, "rb") as _fh:
            prs = Presentation(io.BytesIO(_fh.read()))

        # 清空现有幻灯片（保留母版和版式）
        self._clear_slides(prs)

        layout_count = len(prs.slide_layouts)
        if layout_count == 0:
            raise ValueError("模板未提供任何幻灯片版式")
        fallback_layout_idx = min(1, layout_count - 1)

        for slide_data in slides_data:
            layout_idx = slide_data.get("layout_index", fallback_layout_idx)
            if (not isinstance(layout_idx, int) or isinstance(layout_idx, bool)
                    or not 0 <= layout_idx < layout_count):
                # 负索引不能解释为“倒数第 N 个版式”；所有越界/非法值
                # 统一回退到标题+正文（若模板只有一个版式则回退 0）。
                layout_idx = fallback_layout_idx

            layout = prs.slide_layouts[layout_idx]
            slide = prs.slides.add_slide(layout)

            # 填充占位符
            self._fill_placeholders(slide, slide_data)

        prs.save(str(out))
        return str(out)

    # ==========================================
    # 主题提取（颜色 + 字体）
    # ==========================================

    def _extract_theme(self, prs: PresentationType, config: TemplateConfig):
        """从母版主题 XML 提取颜色和字体"""
        try:
            master = prs.slide_masters[0]
            theme_part = master.part.part_related_by(
                'http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme'
            )
            theme_xml = etree.fromstring(theme_part.blob)

            # 提取颜色方案
            self._parse_color_scheme(theme_xml, config)

            # 提取字体方案
            self._parse_font_scheme(theme_xml, config)

        except Exception as exc:
            # 回退到从幻灯片内容分析
            logger.warning(
                "主题 XML 解析失败，降级为从幻灯片内容提取颜色/字体: %s",
                exc, exc_info=True,
            )
            self._fallback_extract_colors(prs, config)
            self._fallback_extract_fonts(prs, config)

    def _parse_color_scheme(self, theme_xml, config: TemplateConfig):
        """解析 a:clrScheme"""
        clr_scheme = theme_xml.find('.//a:clrScheme', nsmap)
        if clr_scheme is None:
            return

        color_map = {
            'dk1': 'dk1', 'lt1': 'lt1',
            'dk2': 'dk2', 'lt2': 'lt2',
            'accent1': 'accent1', 'accent2': 'accent2',
            'accent3': 'accent3', 'accent4': 'accent4',
            'accent5': 'accent5', 'accent6': 'accent6',
            'hlink': 'hlink', 'folHlink': 'folHlink',
        }

        for child in clr_scheme:
            tag = etree.QName(child.tag).localname
            if tag not in color_map:
                continue

            # 颜色可能是 srgbClr 或 sysClr
            srgb = child.find('a:srgbClr', nsmap)
            sysclr = child.find('a:sysClr', nsmap)

            hex_raw = None
            if srgb is not None:
                candidate = srgb.get('val')
                if _is_hex6(candidate):
                    hex_raw = candidate
            elif sysclr is not None:
                # lastClr 才是真实 hex；val 是 window/lt1 这类系统/主题 token。
                candidate = sysclr.get('lastClr')
                if _is_hex6(candidate):
                    hex_raw = candidate
                else:
                    token = (sysclr.get('val') or '').lower()
                    hex_raw = {
                        'window': 'FFFFFF', 'windowtext': '000000',
                        'lt1': 'FFFFFF', 'dk1': '000000',
                        'lt2': 'E7E6E6', 'dk2': '1F4E79',
                    }.get(token)
            if not hex_raw:
                # 既无合法 hex 也无法解析 token：跳过，避免写出 #lt1（P3-44）
                continue

            setattr(config.colors, color_map[tag], ('#' + hex_raw).upper())

    def _parse_font_scheme(self, theme_xml, config: TemplateConfig):
        """解析 a:fontScheme"""
        font_scheme = theme_xml.find('.//a:fontScheme', nsmap)
        if font_scheme is None:
            return

        # majorFont（标题）
        major = font_scheme.find('a:majorFont', nsmap)
        if major is not None:
            latin = major.find('a:latin', nsmap)
            ea = major.find('a:ea', nsmap)
            if latin is not None:
                val = latin.get('typeface', '')
                config.fonts.major_latin = val or 'Calibri'
            if ea is not None:
                val = ea.get('typeface', '')
                config.fonts.major_ea = val or '微软雅黑'

        # minorFont（正文）
        minor = font_scheme.find('a:minorFont', nsmap)
        if minor is not None:
            latin = minor.find('a:latin', nsmap)
            ea = minor.find('a:ea', nsmap)
            if latin is not None:
                val = latin.get('typeface', '')
                config.fonts.minor_latin = val or 'Calibri'
            if ea is not None:
                val = ea.get('typeface', '')
                config.fonts.minor_ea = val or '微软雅黑'

    # ==========================================
    # 母版提取
    # ==========================================

    def _extract_masters(self, prs: PresentationType, config: TemplateConfig):
        """提取所有母版信息"""
        for i, master in enumerate(prs.slide_masters):
            master_info = MasterInfo(
                index=i,
                name=master.name if hasattr(master, 'name') else f"Master{i+1}",
                layout_count=len(master.slide_layouts),
            )

            # 母版级别的占位符
            for ph in master.placeholders:
                ph_info = self._extract_placeholder(ph)
                if ph_info.ph_type in ('title', 'ctrTitle'):
                    master_info.title_placeholder = ph_info
                elif ph_info.ph_type in ('body', 'obj'):
                    master_info.body_placeholder = ph_info

            config.masters.append(master_info)

    # ==========================================
    # 版式提取
    # ==========================================

    def _extract_all_layouts(self, prs: PresentationType, config: TemplateConfig):
        """提取所有版式及其占位符位置"""
        for i, layout in enumerate(prs.slide_layouts):
            layout_info = LayoutInfo(
                index=i,
                name=layout.name,
                placeholder_count=len(layout.placeholders),
            )

            content_count = 0
            for ph in layout.placeholders:  # type: ignore[misc]
                ph_info = self._extract_placeholder(ph)
                layout_info.placeholders.append(ph_info)

                # 分类
                ph_type = ph_info.ph_type
                if ph_type in ('title', 'ctrTitle'):
                    layout_info.has_title = True
                    layout_info.title_left = ph_info.left
                    layout_info.title_top = ph_info.top
                    layout_info.title_width = ph_info.width
                    layout_info.title_height = ph_info.height
                elif ph_type in ('body', 'obj', 'subTitle'):
                    content_count += 1
                    if content_count == 1:
                        layout_info.has_content = True
                        layout_info.content_left = ph_info.left
                        layout_info.content_top = ph_info.top
                        layout_info.content_width = ph_info.width
                        layout_info.content_height = ph_info.height
                    elif content_count == 2:
                        layout_info.has_two_content = True
                elif ph_type == 'pic':
                    layout_info.has_picture = True
                elif ph_type == 'chart':
                    layout_info.has_chart = True
                elif ph_type == 'tbl':
                    layout_info.has_table = True

            # 推断版式类型
            layout_info.layout_type = self._infer_layout_type(layout_info)

            config.layouts.append(layout_info)

    def _extract_placeholder(self, placeholder) -> PlaceholderInfo:
        """提取单个占位符的详细信息"""
        try:
            phf = placeholder.placeholder_format
            idx = phf.idx
            ph_type = str(phf.type).split('.')[-1].lower() if phf.type else "unknown"
        except Exception:
            idx = getattr(placeholder, 'placeholder_format', None)
            idx = idx.idx if idx else 0
            ph_type = "unknown"

        # 标准化类型名
        ph_type = self._normalize_ph_type(ph_type)

        info = PlaceholderInfo(
            idx=idx,
            ph_type=ph_type,
            name=placeholder.name if hasattr(placeholder, 'name') else "",
        )

        # 位置和大小
        try:
            info.left = placeholder.left / 914400 if placeholder.left else 0
            info.top = placeholder.top / 914400 if placeholder.top else 0
            info.width = placeholder.width / 914400 if placeholder.width else 0
            info.height = placeholder.height / 914400 if placeholder.height else 0
        except Exception:
            pass

        # 字体信息
        try:
            if placeholder.has_text_frame:
                for para in placeholder.text_frame.paragraphs[:1]:
                    if para.runs:
                        run = para.runs[0]
                        if run.font.size:
                            info.font_size = run.font.size.pt
                        if run.font.name:
                            info.font_name = run.font.name
                        info.font_bold = bool(run.font.bold)
                    if para.alignment is not None:
                        info.alignment = str(para.alignment).split('.')[-1].lower()
        except Exception:
            pass

        return info

    @staticmethod
    def _normalize_ph_type(ph_type: str) -> str:
        """标准化占位符类型名"""
        mapping = {
            'title': 'title',
            'ctr_title': 'ctrTitle',
            'ctrtitle': 'ctrTitle',
            'subtitle': 'subTitle',
            'body': 'body',
            'obj': 'obj',
            'object': 'obj',
            'pic': 'pic',
            'picture': 'pic',
            'chart': 'chart',
            'tbl': 'table',
            'table': 'table',
            'dt': 'datetime',
            'ftr': 'footer',
            'sldnum': 'slideNumber',
            'header': 'header',
        }
        return mapping.get(ph_type.lower(), ph_type.lower())

    def _infer_layout_type(self, layout: LayoutInfo) -> str:
        """根据占位符组合推断版式类型"""
        key = (
            layout.has_title,
            layout.has_content,
            layout.has_picture,
            layout.has_two_content,
            layout.has_chart,
        )

        # 特殊名称判断
        name_lower = layout.name.lower()
        if 'cover' in name_lower or '标题幻灯片' in layout.name or 'title slide' in name_lower:
            return 'cover'
        if 'section' in name_lower or '节' in layout.name:
            return 'section'
        if 'two' in name_lower or '两栏' in layout.name or '对比' in layout.name:
            return 'two_column'
        if 'picture' in name_lower or '图片' in layout.name:
            return 'content_image'
        if 'blank' in name_lower or '空白' in layout.name:
            return 'blank'
        if 'title only' in name_lower or '仅标题' in layout.name:
            return 'title_only'

        return self.LAYOUT_TYPE_MAP.get(key, 'content')

    # ==========================================
    # 背景提取
    # ==========================================

    def _extract_background(self, prs: PresentationType, config: TemplateConfig):
        """提取背景色"""
        try:
            # 先从母版提取
            master = prs.slide_masters[0]
            bg = master.background
            if bg.fill.type is not None:
                fill_type = str(bg.fill.type).split('.')[-1].lower()
                config.bg_fill_type = fill_type
                if fill_type == 'solid':
                    rgb = self._get_fill_color(bg.fill)
                    if rgb:
                        config.bg_color = rgb
        except Exception:
            pass

        # 从第一张幻灯片补充
        if config.slide_count > 0:
            try:
                slide = prs.slides[0]
                bg = slide.background
                if bg.fill.type is not None:
                    rgb = self._get_fill_color(bg.fill)
                    if rgb:
                        config.bg_color = rgb
            except Exception:
                pass

    @staticmethod
    def _get_fill_color(fill) -> Optional[str]:
        """获取填充颜色"""
        try:
            if hasattr(fill, 'fore_color') and fill.fore_color.rgb:
                return f'#{fill.fore_color.rgb}'
        except Exception:
            pass
        return None

    # ==========================================
    # 从示例幻灯片补充分析
    # ==========================================

    def _analyze_sample_slides(self, prs: PresentationType, config: TemplateConfig):
        """分析模板中的示例幻灯片，补充字体和颜色信息"""
        title_sizes = []
        body_sizes = []
        title_fonts = []
        body_fonts = []

        for slide in prs.slides:
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        if not run.font.size:
                            continue
                        size = run.font.size.pt
                        font_name = run.font.name or ""
                        if size >= 24:
                            title_sizes.append(size)
                            if font_name:
                                title_fonts.append(font_name)
                        elif size >= 10:
                            body_sizes.append(size)
                            if font_name:
                                body_fonts.append(font_name)

        if title_sizes:
            config.fonts.major_size = Counter(title_sizes).most_common(1)[0][0]
        if body_sizes:
            config.fonts.minor_size = Counter(body_sizes).most_common(1)[0][0]

        if title_fonts and not config.fonts.major_ea:
            config.fonts.major_ea = Counter(title_fonts).most_common(1)[0][0]
        if body_fonts and not config.fonts.minor_ea:
            config.fonts.minor_ea = Counter(body_fonts).most_common(1)[0][0]

    # ==========================================
    # 回退方案（无法读取主题 XML 时）
    # ==========================================

    def _fallback_extract_colors(self, prs: PresentationType, config: TemplateConfig):
        """从幻灯片内容回退提取颜色"""
        colors = []
        for slide in prs.slides:
            for shape in slide.shapes:
                try:
                    if hasattr(shape, 'fill') and shape.fill.type is not None:
                        rgb = self._get_fill_color(shape.fill)
                        if rgb:
                            colors.append(rgb)
                except Exception:
                    pass
        if colors:
            config.colors.dk2 = Counter(colors).most_common(1)[0][0]
            config.colors.accent1 = config.colors.dk2

    def _fallback_extract_fonts(self, prs: PresentationType, config: TemplateConfig):
        """从幻灯片内容回退提取字体"""
        fonts = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        for run in para.runs:
                            if run.font.name:
                                fonts.append(run.font.name)
        if fonts:
            most_common = Counter(fonts).most_common(1)[0][0]
            config.fonts.major_ea = most_common
            config.fonts.minor_ea = most_common

    # ==========================================
    # 基于模板创建 PPT
    # ==========================================

    @staticmethod
    def _clear_slides(prs: PresentationType):
        """清空所有幻灯片（保留母版和版式），委托给模块级共享实现"""
        clear_slides(prs)

    def _fill_placeholders(self, slide, slide_data: dict):
        """填充幻灯片占位符"""
        for placeholder in slide.placeholders:
            try:
                phf = placeholder.placeholder_format
                idx = phf.idx
                ph_type = str(phf.type).split('.')[-1].lower() if phf.type else ""
            except Exception:
                continue

            # 标题
            if ph_type in ('title', 'ctr_title', 'ctrtitle'):
                if "title" in slide_data:
                    placeholder.text = str(slide_data["title"])
                continue

            # 副标题
            if ph_type in ('subtitle', 'sub_title'):
                if "subtitle" in slide_data:
                    placeholder.text = str(slide_data["subtitle"])
                continue

            # 正文/内容
            if ph_type in ('body', 'obj', 'object'):
                content = (
                    slide_data.get("bullets") or
                    slide_data.get("content") or
                    slide_data.get("body_text") or
                    slide_data.get(str(idx))
                )
                if content is not None:
                    self._fill_content(placeholder, content)
                continue

            # 其他占位符按索引匹配
            if str(idx) in slide_data:
                try:
                    placeholder.text = str(slide_data[str(idx)])
                except Exception:
                    pass

    @staticmethod
    def _fill_content(placeholder, content):
        """填充内容占位符（支持列表和纯文本）"""
        if not placeholder.has_text_frame:
            return

        tf = placeholder.text_frame
        tf.clear()

        def _clamp_level(v):
            try:
                return max(0, min(8, int(v)))
            except (TypeError, ValueError):
                return 0

        if isinstance(content, (list, tuple)):
            for i, item in enumerate(content):
                if i == 0:
                    p = tf.paragraphs[0]
                else:
                    p = tf.add_paragraph()
                p.text = str(item)
                p.level = 0
        elif isinstance(content, dict):
            items = content.get("bullets", content.get("items", [str(content)]))
            for i, item in enumerate(items):
                if i == 0:
                    p = tf.paragraphs[0]
                else:
                    p = tf.add_paragraph()
                if isinstance(item, dict):
                    p.text = str(item.get("text", ""))
                    p.level = _clamp_level(item.get("level", 0))
                else:
                    p.text = str(item)
        else:
            tf.text = str(content)


# ==========================================
# 便捷函数
# ==========================================

def analyze_template(template_path: str) -> TemplateConfig:
    """便捷函数：分析模板"""
    return TemplateAnalyzer().analyze(template_path)


# 向后兼容别名
TemplateInfo = TemplateConfig
