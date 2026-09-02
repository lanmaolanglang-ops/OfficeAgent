"""
Vision Gateway 数据模型
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Union
from enum import Enum
from pathlib import Path
import base64


class VisionProvider(Enum):
    """视觉模型提供商"""
    OPENAI = "openai"           # GPT-4V, GPT-4o
    GEMINI = "gemini"           # Gemini Pro Vision
    CLAUDE = "claude"           # Claude 3 Opus/Sonnet
    DOUBAO = "doubao"           # 豆包视觉
    QWEN = "qwen"               # 通义千问VL
    CUSTOM = "custom"           # 自定义 OpenAI 兼容


class VisionTaskType(Enum):
    """视觉任务类型"""
    GENERAL = "general"             # 通用理解
    OCR = "ocr"                     # 文字识别
    TABLE_EXTRACT = "table"         # 表格提取
    CHART_READ = "chart"            # 图表读取
    LAYOUT_ANALYSIS = "layout"      # 布局分析
    DOCUMENT_UNDERSTANDING = "doc"  # 文档理解
    FORMULA_READ = "formula"        # 公式识别
    IMAGE_DESCRIBE = "describe"     # 图片描述
    QUALITY_CHECK = "quality"       # 质量检查


class ImageSource(Enum):
    """图片来源"""
    FILE = "file"           # 本地文件
    URL = "url"             # URL
    BASE64 = "base64"       # base64
    BUFFER = "buffer"       # 字节流


@dataclass
class ImageInput:
    """图片输入"""
    source: ImageSource = ImageSource.FILE
    path: str = ""
    url: str = ""
    base64_data: str = ""
    mime_type: str = ""
    buffer: bytes = b""
    width: int = 0
    height: int = 0
    page_number: int = 0       # 文档页码（PDF/PPT截图时）
    label: str = ""            # 图片标签/描述

    def to_base64(self) -> str:
        """转为 base64"""
        if self.source == ImageSource.BASE64:
            return self.base64_data
        if self.source == ImageSource.FILE and self.path:
            with open(self.path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        if self.source == ImageSource.BUFFER and self.buffer:
            return base64.b64encode(self.buffer).decode("utf-8")
        return ""

    def get_mime(self) -> str:
        """获取 MIME 类型"""
        if self.mime_type:
            return self.mime_type
        if self.path:
            ext = Path(self.path).suffix.lower()
            mime_map = {
                ".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".gif": "image/gif",
                ".bmp": "image/bmp", ".webp": "image/webp",
                ".tiff": "image/tiff",
            }
            return mime_map.get(ext, "image/png")
        return "image/png"

    @classmethod
    def from_file(cls, path: str, page_number: int = 0, label: str = "") -> "ImageInput":
        return cls(source=ImageSource.FILE, path=path, page_number=page_number, label=label)

    @classmethod
    def from_url(cls, url: str) -> "ImageInput":
        return cls(source=ImageSource.URL, url=url)

    @classmethod
    def from_base64(cls, data: str, mime_type: str = "image/png") -> "ImageInput":
        return cls(source=ImageSource.BASE64, base64_data=data, mime_type=mime_type)

    @classmethod
    def from_buffer(cls, buf: bytes, mime_type: str = "image/png") -> "ImageInput":
        return cls(source=ImageSource.BUFFER, buffer=buf, mime_type=mime_type)


@dataclass
class DetectedElement:
    """检测到的元素"""
    element_type: str = ""       # text, table, chart, image, heading, paragraph, list, formula
    bbox: List[float] = field(default_factory=list)  # [x1, y1, x2, y2] 相对坐标 0-1
    content: str = ""
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TableData:
    """提取的表格数据"""
    rows: int = 0
    cols: int = 0
    headers: List[str] = field(default_factory=list)
    data: List[List[str]] = field(default_factory=list)
    bbox: List[float] = field(default_factory=list)

    def to_list(self) -> List[List[str]]:
        result = []
        if self.headers:
            result.append(self.headers)
        result.extend(self.data)
        return result


@dataclass
class ChartData:
    """读取的图表数据"""
    chart_type: str = ""         # bar, line, pie, column, scatter, area
    title: str = ""
    categories: List[str] = field(default_factory=list)
    series: List[Dict[str, Any]] = field(default_factory=list)
    bbox: List[float] = field(default_factory=list)


@dataclass
class StructuredVisionResult:
    """结构化视觉结果"""
    raw_text: str = ""
    summary: str = ""
    language: str = "zh"
    elements: List[DetectedElement] = field(default_factory=list)
    tables: List[TableData] = field(default_factory=list)
    charts: List[ChartData] = field(default_factory=list)
    headings: List[str] = field(default_factory=list)
    key_values: Dict[str, str] = field(default_factory=dict)
    full_text: str = ""

    def get_primary_table(self) -> Optional[TableData]:
        return self.tables[0] if self.tables else None

    def get_all_text(self) -> str:
        if self.full_text:
            return self.full_text
        parts = []
        for el in self.elements:
            if el.content:
                parts.append(el.content)
        return "\n".join(parts)


@dataclass
class VisionRequest:
    """视觉请求"""
    images: List[ImageInput] = field(default_factory=list)
    prompt: str = ""
    task_type: VisionTaskType = VisionTaskType.GENERAL
    system_prompt: str = ""
    max_tokens: int = 4096
    temperature: float = 0.1
    require_structured: bool = True   # 是否要求结构化输出
    language: str = "zh"
    extra: Dict[str, Any] = field(default_factory=dict)

    def add_image(self, img: ImageInput):
        self.images.append(img)
        return self


@dataclass
class VisionResponse:
    """视觉响应"""
    success: bool = False
    content: str = ""
    structured: Optional[StructuredVisionResult] = None
    model_used: str = ""
    provider: str = ""
    tokens_used: int = 0
    latency_ms: int = 0
    error: str = ""
    raw_response: Any = None

    @property
    def text(self) -> str:
        return self.content

    def get_tables(self) -> List[TableData]:
        return self.structured.tables if self.structured else []

    def get_charts(self) -> List[ChartData]:
        return self.structured.charts if self.structured else []


@dataclass
class DocumentPage:
    """文档页面"""
    page_number: int = 1
    image: Optional[ImageInput] = None
    width: int = 0
    height: int = 0
    text_hint: str = ""


@dataclass
class DocumentVisionResult:
    """多页文档视觉结果"""
    file_path: str = ""
    file_type: str = ""          # pdf, pptx, ppt, image
    page_count: int = 0
    pages: List[DocumentPage] = field(default_factory=list)
    responses: List[VisionResponse] = field(default_factory=list)
    combined_text: str = ""
    combined_result: Optional[StructuredVisionResult] = None
    tables: List[TableData] = field(default_factory=list)
    error: str = ""
    successful_pages: int = 0
    failed_pages: int = 0
    failed_page_numbers: List[int] = field(default_factory=list)
    partial: bool = False

    @property
    def success(self) -> bool:
        return self.error == "" and self.successful_pages > 0

    def get_all_tables(self) -> List[TableData]:
        tables = list(self.tables)
        for resp in self.responses:
            if resp.structured:
                tables.extend(resp.structured.tables)
        return tables

    def get_full_text(self) -> str:
        if self.combined_text:
            return self.combined_text
        parts = []
        for resp in self.responses:
            if resp.content:
                parts.append(resp.content)
        return "\n\n".join(parts)
