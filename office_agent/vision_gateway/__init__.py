"""
Vision Gateway - 多模态视觉理解模块

支持 GPT Vision、Gemini Vision、Claude Vision、豆包视觉等多模型。
支持图片、PDF、PPT、扫描文档的理解。
"""
from .vision_models import (
    VisionRequest, VisionResponse, StructuredVisionResult,
    ImageInput, DocumentPage, DocumentVisionResult,
    VisionTaskType, VisionProvider, ImageSource,
    TableData, ChartData, DetectedElement,
)
from .gateway import VisionGateway
from .document_renderer import DocumentRenderer
from .clients import (
    BaseVisionClient,
    OpenAIVisionClient,
    GeminiVisionClient,
    ClaudeVisionClient,
    DoubaoVisionClient,
)

__all__ = [
    # Gateway
    "VisionGateway",
    "DocumentRenderer",
    # Models
    "VisionRequest", "VisionResponse", "StructuredVisionResult",
    "ImageInput", "DocumentPage", "DocumentVisionResult",
    "VisionTaskType", "VisionProvider", "ImageSource",
    "TableData", "ChartData", "DetectedElement",
    # Clients
    "BaseVisionClient",
    "OpenAIVisionClient",
    "GeminiVisionClient",
    "ClaudeVisionClient",
    "DoubaoVisionClient",
]
