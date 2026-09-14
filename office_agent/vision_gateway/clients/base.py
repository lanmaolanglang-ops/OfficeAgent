"""
视觉模型客户端基类
"""
import time
import json
import re
from abc import ABC, abstractmethod
from typing import List, Any
from urllib import request

from ..vision_models import (
    VisionRequest, VisionResponse, StructuredVisionResult,
    TableData, ChartData, DetectedElement,
)


class BaseVisionClient(ABC):
    """视觉模型客户端基类"""

    def __init__(self, api_key: str, model: str, base_url: str = "",
                 display_name: str = ""):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.display_name = display_name or model
        self.timeout = 60

    @abstractmethod
    def analyze(self, request: VisionRequest) -> VisionResponse:
        """执行视觉分析"""
        pass

    def _make_response(self, content: str, start_time: float,
                       tokens: int = 0, raw: Any = None) -> VisionResponse:
        if not isinstance(content, str) or not content.strip():
            return self._make_error("模型返回空内容", start_time)
        return VisionResponse(
            success=True,
            content=content,
            model_used=self.model,
            provider=self.provider_name,
            tokens_used=tokens,
            latency_ms=int((time.time() - start_time) * 1000),
            raw_response=raw,
        )

    def _make_error(self, err: str, start_time: float) -> VisionResponse:
        return VisionResponse(
            success=False,
            error=err,
            model_used=self.model,
            provider=self.provider_name,
            latency_ms=int((time.time() - start_time) * 1000),
        )

    @property
    def provider_name(self) -> str:
        return self.__class__.__name__.replace("VisionClient", "").lower()

    def _http_post(self, url: str, headers: dict, data: bytes) -> dict:
        """HTTP POST 请求（使用标准库）"""
        req = request.Request(url, data=data, headers=headers, method="POST")
        with request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)

    def _parse_structured(self, text: str, request: VisionRequest) -> StructuredVisionResult:
        """将模型返回的文本解析为结构化结果"""
        result = StructuredVisionResult(
            raw_text=text,
            full_text=text,
        )

        # 尝试解析 JSON
        json_str = self._extract_json(text)
        if json_str:
            try:
                data = json.loads(json_str, strict=False)
                if isinstance(data, dict):
                    self._fill_structured(result, data)
                    return result
            except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
                pass

        # 提取表格（markdown 表格）
        result.tables = self._extract_markdown_tables(text)

        # 提取标题
        result.headings = re.findall(r'^#{1,4}\s+(.+)$', text, re.MULTILINE)

        return result

    def _extract_json(self, text: str) -> str:
        """从文本中提取 JSON（处理嵌套花括号）"""
        # 去掉 markdown 代码块标记
        text = re.sub(r'```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*```', '', text)

        # 找到第一个 { 或 [
        start = -1
        for i, ch in enumerate(text):
            if ch in '{[':
                start = i
                break

        if start < 0:
            return ""

        # 使用栈并感知字符串与转义，避免 JSON 字符串里的 {}[] 干扰边界。
        pairs = {'}': '{', ']': '['}
        stack = []
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == '\\':
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in '{[':
                stack.append(ch)
            elif ch in '}]':
                if not stack or stack[-1] != pairs[ch]:
                    return ""
                stack.pop()
                if not stack:
                    return text[start:i+1]

        return ""

    def _fill_structured(self, result: StructuredVisionResult, data: dict):
        """从 JSON 填充结构化结果"""
        if "summary" in data:
            result.summary = str(data["summary"])
        if "full_text" in data or "text" in data:
            result.full_text = str(data.get("full_text") or data.get("text", ""))
        if "language" in data:
            result.language = str(data["language"])

        # 表格
        if "tables" in data and isinstance(data["tables"], list):
            for t in data["tables"]:
                if not isinstance(t, dict):
                    continue
                try:
                    table = TableData(
                        rows=t.get("rows", 0),
                        cols=t.get("cols", 0),
                        headers=[str(h) for h in t["headers"]] if isinstance(t.get("headers"), list) else [],
                        data=[[str(cell) for cell in row] for row in t["data"] if isinstance(row, list)] if isinstance(t.get("data"), list) else [],
                    )
                    result.tables.append(table)
                except Exception:
                    continue

        # 图表
        if "charts" in data and isinstance(data["charts"], list):
            for c in data["charts"]:
                if not isinstance(c, dict):
                    continue
                try:
                    chart = ChartData(
                        chart_type=c.get("type", ""),
                        title=c.get("title", ""),
                        categories=[str(x) for x in c["categories"]] if isinstance(c.get("categories"), list) else [],
                        series=[item for item in c["series"] if isinstance(item, dict)] if isinstance(c.get("series"), list) else [],
                    )
                    result.charts.append(chart)
                except Exception:
                    continue

        # 键值对
        if "key_values" in data and isinstance(data["key_values"], dict):
            result.key_values = data["key_values"]

        # 元素
        if "elements" in data and isinstance(data["elements"], list):
            for el in data["elements"]:
                if not isinstance(el, dict):
                    continue
                confidence = el.get("confidence", 0)
                try:
                    confidence = float(confidence) if confidence is not None else 0.0
                except (TypeError, ValueError):
                    confidence = 0.0
                elem = DetectedElement(
                    element_type=el.get("type", ""),
                    content=el.get("content", ""),
                    confidence=confidence,
                    bbox=el.get("bbox", []),
                )
                result.elements.append(elem)

        # 标题
        if "headings" in data and isinstance(data["headings"], list):
            result.headings = [str(h) for h in data["headings"]]

    def _extract_markdown_tables(self, text: str) -> List[TableData]:
        """从 Markdown 文本中提取表格"""
        tables = []
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            if "|" in lines[i] and i + 1 < len(lines) and re.match(r'^[\s|:-]+$', lines[i + 1]):
                # 找到表格开始
                def split_row(line: str) -> List[str]:
                    stripped = line.strip()
                    if stripped.startswith("|"):
                        stripped = stripped[1:]
                    if stripped.endswith("|"):
                        stripped = stripped[:-1]
                    return [cell.strip() for cell in stripped.split("|")]

                header_line = lines[i].strip()
                headers = split_row(header_line)
                data_rows = []
                j = i + 2
                while j < len(lines) and "|" in lines[j]:
                    row = split_row(lines[j])
                    if row:
                        data_rows.append(row)
                    j += 1
                if headers:
                    tables.append(TableData(
                        rows=len(data_rows) + 1,
                        cols=len(headers),
                        headers=headers,
                        data=data_rows,
                    ))
                i = j
            else:
                i += 1
        return tables

    def _build_system_prompt(self, request: VisionRequest) -> str:
        """构建系统提示词"""
        if request.system_prompt:
            return request.system_prompt

        task_prompts = {
            "ocr": "你是一个专业的OCR引擎。请准确识别图片中的所有文字，保持原始排版和层级结构。",
            "table": "你是一个表格提取专家。请识别图片中的表格，以JSON格式返回表格的行列结构和所有单元格内容。",
            "chart": "你是一个图表数据读取专家。请识别图表类型、标题、坐标轴标签、图例和所有数据点，以JSON格式返回。",
            "layout": "你是一个文档布局分析专家。请分析页面布局，识别标题、段落、表格、图片、列表等元素及其位置。",
            "doc": "你是一个文档理解专家。请全面理解文档内容，提取关键信息、数据和结构。",
            "formula": "你是一个公式识别专家。请识别图片中的数学公式，以LaTeX格式返回。",
            "describe": "你是一个图片描述专家。请详细描述图片中的内容。",
            "quality": "你是一个文档质量检查专家。请检查文档中的格式问题、排版错误、数据异常等。",
            "general": "你是一个多模态视觉理解助手。请分析图片内容并提供详细、准确的描述。",
        }

        base = task_prompts.get(request.task_type.value, task_prompts["general"])

        if request.require_structured:
            base += """

请以JSON格式返回结果，包含以下字段（根据实际内容填充）：
{
  "summary": "内容摘要",
  "full_text": "完整文字内容",
  "language": "zh/en",
  "headings": ["标题1", "标题2"],
  "tables": [{"headers": [], "data": [[], []]}],
  "charts": [{"type": "bar", "title": "", "categories": [], "series": [{"name": "", "values": []}]}],
  "key_values": {"键": "值"}
}

如果内容中没有表格、图表等，对应字段返回空数组。只返回JSON，不要其他解释。"""

        return base

    def _build_user_prompt(self, request: VisionRequest) -> str:
        """构建用户提示词"""
        prompt = request.prompt
        if not prompt:
            prompt_map = {
                "ocr": "请识别图片中的所有文字",
                "table": "请提取图片中的表格数据",
                "chart": "请读取图表中的数据",
                "layout": "请分析页面布局结构",
                "doc": "请理解文档内容并提取关键信息",
                "general": "请描述图片中的内容",
            }
            prompt = prompt_map.get(request.task_type.value, "请分析这张图片")
        return prompt
