"""
Vision Gateway - 多模态视觉理解网关

统一管理多个视觉模型，支持图片、PDF、PPT、扫描文档的理解。

使用方式：
    gateway = VisionGateway()

    # 添加模型
    gateway.add_openai("sk-xxx", model="gpt-4o")
    gateway.add_gemini("key", model="gemini-1.5-pro")
    gateway.add_claude("key", model="claude-3-sonnet")
    gateway.add_doubao("key", model="doubao-1.5-vision-pro")

    # 分析图片
    result = gateway.analyze_image("chart.png", task="chart")
    print(result.structured.tables)

    # 分析 PDF
    result = gateway.analyze_document("report.pdf", task="doc")
    for page in result.responses:
        print(page.content)

    # OCR
    result = gateway.ocr("scan.jpg")
    print(result.text)
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from typing import Optional, List, Dict, Union

from .vision_models import (
    VisionRequest, VisionResponse, StructuredVisionResult,
    ImageInput, DocumentPage, DocumentVisionResult,
    VisionTaskType,
    TableData, ChartData,
)
from .clients.base import BaseVisionClient
from .clients import (
    OpenAIVisionClient, GeminiVisionClient,
    ClaudeVisionClient, DoubaoVisionClient,
)
from .document_renderer import DocumentRenderer


class VisionGateway:
    """多模态视觉理解网关"""

    def __init__(self, default_model: Optional[str] = None,
                 dpi: int = 200, output_dir: Optional[str] = None):
        """
        Args:
            default_model: 默认使用的模型名称
            dpi: 文档渲染 DPI
            output_dir: 临时文件目录
        """
        self.clients: Dict[str, BaseVisionClient] = {}
        self.client_order: List[str] = []
        self.default_model = default_model
        self.renderer = DocumentRenderer(dpi=dpi, output_dir=output_dir)
        self._renderer_lock = threading.RLock()

    # === 模型管理 ===

    def add_openai(self, api_key: str, model: str = "gpt-4o",
                   base_url: str = "https://api.openai.com/v1",
                   name: str = "") -> "VisionGateway":
        client = OpenAIVisionClient(api_key, model, base_url, name)
        key = name or f"openai:{model}"
        self.clients[key] = client
        if key not in self.client_order:
            self.client_order.append(key)
        if not self.default_model:
            self.default_model = key
        return self

    def add_gemini(self, api_key: str, model: str = "gemini-1.5-pro",
                   base_url: str = "https://generativelanguage.googleapis.com/v1beta",
                   name: str = "") -> "VisionGateway":
        client = GeminiVisionClient(api_key, model, base_url, name)
        key = name or f"gemini:{model}"
        self.clients[key] = client
        if key not in self.client_order:
            self.client_order.append(key)
        if not self.default_model:
            self.default_model = key
        return self

    def add_claude(self, api_key: str, model: str = "claude-3-sonnet-20240229",
                   base_url: str = "https://api.anthropic.com/v1",
                   name: str = "") -> "VisionGateway":
        client = ClaudeVisionClient(api_key, model, base_url, name)
        key = name or f"claude:{model}"
        self.clients[key] = client
        if key not in self.client_order:
            self.client_order.append(key)
        if not self.default_model:
            self.default_model = key
        return self

    def add_doubao(self, api_key: str, model: str = "doubao-1.5-vision-pro",
                   base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
                   name: str = "") -> "VisionGateway":
        client = DoubaoVisionClient(api_key, model, base_url, name)
        key = name or f"doubao:{model}"
        self.clients[key] = client
        if key not in self.client_order:
            self.client_order.append(key)
        if not self.default_model:
            self.default_model = key
        return self

    def add_custom(self, client: BaseVisionClient, name: str = "") -> "VisionGateway":
        """添加自定义视觉客户端"""
        key = name or f"custom:{client.model}"
        self.clients[key] = client
        if key not in self.client_order:
            self.client_order.append(key)
        return self

    def set_default(self, model_key: str):
        """设置默认模型"""
        if model_key in self.clients:
            self.default_model = model_key

    def list_models(self) -> List[dict]:
        """列出可用模型"""
        return [
            {
                "key": key,
                "name": self.clients[key].display_name,
                "model": self.clients[key].model,
                "provider": self.clients[key].provider_name,
                "is_default": key == self.default_model,
            }
            for key in self.client_order
        ]

    # === 核心分析接口 ===

    def analyze(self, request: VisionRequest,
                model_key: Optional[str] = None) -> VisionResponse:
        """
        执行视觉分析

        Args:
            request: 视觉请求
            model_key: 指定模型（None 使用默认）
        """
        if not self.clients:
            return VisionResponse(
                success=False,
                error="未配置任何视觉模型，请先 add_openai/add_gemini/add_claude/add_doubao",
            )

        # 选择模型
        keys = [model_key] if model_key else list(self.client_order)
        if model_key and model_key not in self.clients:
            return VisionResponse(
                success=False,
                error=f"模型 {model_key} 不存在",
            )

        # 尝试调用（带故障转移）
        last_error = ""
        for key in keys:
            if key not in self.clients:
                continue
            client = self.clients[key]
            try:
                resp = client.analyze(request)
                if resp.success:
                    return resp
                last_error = resp.error
            except Exception as e:
                last_error = str(e)
                continue

        return VisionResponse(
            success=False,
            error=f"所有视觉模型调用失败: {last_error}",
        )

    def analyze_image(self, image_path: str,
                      prompt: str = "",
                      task: Union[str, VisionTaskType] = "general",
                      model_key: Optional[str] = None,
                      structured: bool = True) -> VisionResponse:
        """
        分析单张图片

        Args:
            image_path: 图片路径
            prompt: 自定义提示
            task: 任务类型
            model_key: 指定模型
            structured: 是否要求结构化输出
        """
        task_type = self._parse_task(task)
        img = ImageInput.from_file(image_path)

        request = VisionRequest(
            images=[img],
            prompt=prompt,
            task_type=task_type,
            require_structured=structured,
        )
        return self.analyze(request, model_key)

    def analyze_images(self, image_paths: List[str],
                       prompt: str = "",
                       task: Union[str, VisionTaskType] = "general",
                       model_key: Optional[str] = None) -> VisionResponse:
        """分析多张图片"""
        task_type = self._parse_task(task)
        images = [ImageInput.from_file(p) for p in image_paths]

        request = VisionRequest(
            images=images,
            prompt=prompt,
            task_type=task_type,
        )
        return self.analyze(request, model_key)

    def analyze_document(self, file_path: str,
                         prompt: str = "",
                         task: Union[str, VisionTaskType] = "doc",
                         model_key: Optional[str] = None,
                         max_pages: int = 30,
                         dpi: Optional[int] = None,
                         max_concurrency: int = 4) -> DocumentVisionResult:
        """
        分析文档（PDF/PPT/图片）

        逐页渲染并分析，合并结果。

        Args:
            file_path: 文件路径
            prompt: 自定义提示
            task: 任务类型
            model_key: 指定模型
            max_pages: 最大页数
            dpi: 渲染 DPI
        """
        result = DocumentVisionResult(
            file_path=file_path,
            file_type=file_path.rsplit(".", 1)[-1].lower(),
        )

        try:
            # DocumentRenderer carries mutable dpi/max_pages. Serialize
            # rendering and restore its configuration; rendered filenames are
            # request-unique, so later requests cannot overwrite these pages.
            renderer_lock = getattr(self, "_renderer_lock", None)
            if renderer_lock is None:
                renderer_lock = threading.RLock()
                self._renderer_lock = renderer_lock
            with renderer_lock:
                previous_dpi = self.renderer.dpi
                previous_max_pages = self.renderer.max_pages
                try:
                    if dpi:
                        self.renderer.dpi = dpi
                    self.renderer.max_pages = max(1, int(max_pages))
                    pages = self.renderer.render(file_path)
                    source_page_count = max(
                        len(pages),
                        int(getattr(self.renderer, "last_total_pages", len(pages))),
                    )
                finally:
                    self.renderer.dpi = previous_dpi
                    self.renderer.max_pages = previous_max_pages
        except Exception as e:
            result.error = f"文档渲染失败: {e}"
            return result

        result.page_count = len(pages)
        result.source_page_count = source_page_count
        result.truncated = source_page_count > len(pages)
        result.pages = pages

        if not pages:
            result.error = "文档没有可分析的页面"
            return result

        task_type = self._parse_task(task)

        # 限并发逐页分析。结果最终仍按页码排序，避免并发完成顺序污染文档顺序。
        all_texts = []
        all_tables = []
        all_charts = []

        def analyze_page(page: DocumentPage) -> VisionResponse:
            if not page.image:
                return VisionResponse(success=False, error="页面没有可分析的图像")

            # 构建提示（包含页面文字提示）
            page_prompt = prompt
            if page.text_hint and task_type != VisionTaskType.OCR:
                page_prompt = f"{prompt}\n\n（页面已有文字信息：{page.text_hint[:200]}）"

            request = VisionRequest(
                images=[page.image],
                prompt=page_prompt,
                task_type=task_type,
                require_structured=True,
            )

            try:
                return self.analyze(request, model_key)
            except Exception as exc:
                return VisionResponse(success=False, error=f"页面分析失败: {exc}")

        try:
            worker_count = max(1, min(int(max_concurrency), 8, len(pages)))
        except (TypeError, ValueError):
            worker_count = min(4, len(pages))

        by_page = {}
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="vision-page") as executor:
            futures = {executor.submit(analyze_page, page): page for page in pages}
            for future in as_completed(futures):
                page = futures[future]
                try:
                    by_page[page.page_number] = future.result()
                except Exception as exc:
                    by_page[page.page_number] = VisionResponse(
                        success=False, error=f"页面分析失败: {exc}"
                    )

        for page in pages:
            resp = by_page[page.page_number]
            result.responses.append(resp)

            if resp.success:
                result.successful_pages += 1
                all_texts.append(f"--- 第 {page.page_number} 页 ---\n{resp.content}")
                if resp.structured:
                    all_tables.extend(resp.structured.tables)
                    all_charts.extend(resp.structured.charts)
            else:
                result.failed_pages += 1
                result.failed_page_numbers.append(page.page_number)

        result.partial = result.successful_pages > 0 and result.failed_pages > 0
        if result.successful_pages == 0:
            result.error = f"全部 {result.failed_pages} 页分析失败"

        # 合并结果
        result.combined_text = "\n\n".join(all_texts)
        result.tables = all_tables

        # 构建合并的结构化结果
        result.combined_result = StructuredVisionResult(
            full_text=result.combined_text,
            tables=all_tables,
            charts=all_charts,
        )

        return result

    # === 便捷方法 ===

    def ocr(self, image_path: str,
            model_key: Optional[str] = None) -> VisionResponse:
        """文字识别"""
        return self.analyze_image(image_path, task="ocr",
                                  model_key=model_key, structured=False)

    def extract_table(self, image_path: str,
                      model_key: Optional[str] = None) -> List[TableData]:
        """提取表格"""
        resp = self.analyze_image(image_path, task="table", model_key=model_key)
        return resp.get_tables()

    def read_chart(self, image_path: str,
                   model_key: Optional[str] = None) -> List[ChartData]:
        """读取图表数据"""
        resp = self.analyze_image(image_path, task="chart", model_key=model_key)
        return resp.get_charts()

    def describe(self, image_path: str,
                 model_key: Optional[str] = None) -> str:
        """描述图片"""
        resp = self.analyze_image(image_path, task="describe",
                                  model_key=model_key, structured=False)
        return resp.text if resp.success else resp.error

    # === 内部方法 ===

    @staticmethod
    def _parse_task(task) -> VisionTaskType:
        if isinstance(task, VisionTaskType):
            return task
        task_map = {
            "general": VisionTaskType.GENERAL,
            "ocr": VisionTaskType.OCR,
            "table": VisionTaskType.TABLE_EXTRACT,
            "chart": VisionTaskType.CHART_READ,
            "layout": VisionTaskType.LAYOUT_ANALYSIS,
            "doc": VisionTaskType.DOCUMENT_UNDERSTANDING,
            "formula": VisionTaskType.FORMULA_READ,
            "describe": VisionTaskType.IMAGE_DESCRIBE,
            "quality": VisionTaskType.QUALITY_CHECK,
        }
        return task_map.get(str(task).lower(), VisionTaskType.GENERAL)
