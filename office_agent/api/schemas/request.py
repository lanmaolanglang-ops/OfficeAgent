"""
请求数据模型
"""
from typing import Optional, List, Dict, Any
import re
from pydantic import BaseModel, Field, field_validator

_SENSITIVE = re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*[^\s,;]+")


def _filter_sensitive(value):
    if isinstance(value, str):
        return _SENSITIVE.sub(lambda m: f"{m.group(1)}=[已过滤]", value)
    if isinstance(value, list):
        return [_filter_sensitive(v) for v in value]
    if isinstance(value, dict):
        return {k: _filter_sensitive(v) for k, v in value.items()
                if str(k).lower() not in {"api_key", "password", "secret", "authorization"}}
    return value


class ChatRequest(BaseModel):
    """对话请求"""
    message: str = Field(..., description="用户自然语言需求", min_length=1, max_length=10000)
    file_ids: Optional[List[str]] = Field(default=None, description="关联文件ID列表")
    conversation_id: Optional[str] = Field(default=None, description="会话ID")
    context: Optional[Dict[str, Any]] = Field(default=None, description="额外上下文")
    agent_hint: Optional[str] = Field(default=None, description="指定Agent（可选）")
    template_file_id: Optional[str] = Field(default=None, description="PPT 模板文件ID（可选，用于按模板生成）")

    @field_validator("message")
    @classmethod
    def filter_message_secrets(cls, value: str) -> str:
        return _filter_sensitive(value)

    @field_validator("file_ids")
    @classmethod
    def enforce_single_primary_file(cls, value):
        if value and len(value) > 1:
            raise ValueError("当前 Office 任务只支持一个主文件，请一次附加一个文件")
        return value

    @field_validator("context", mode="before")
    @classmethod
    def filter_context_secrets(cls, value):
        return _filter_sensitive(value)

    @field_validator("agent_hint", mode="before")
    @classmethod
    def normalize_agent_hint(cls, value):
        if value is None:
            return None
        hint = str(value).strip().lower()
        if hint.endswith("_agent"):
            hint = hint[:-6]
        if hint in {"orchestrator", "workflow"}:
            hint = "auto"
        if hint not in {"auto", "word", "ppt", "excel"}:
            raise ValueError("agent_hint 仅支持 auto/word/ppt/excel")
        return hint


class TaskCreateRequest(BaseModel):
    """创建任务请求"""
    task_type: str = Field(..., description="任务类型：word_format/ppt_generate/excel_analysis等")
    instruction: str = Field(..., description="任务指令", min_length=1)
    file_ids: Optional[List[str]] = Field(default=None, description="输入文件ID列表")
    output_format: Optional[str] = Field(default=None, description="输出格式")
    options: Optional[Dict[str, Any]] = Field(default=None, description="任务选项")
    callback_url: Optional[str] = Field(default=None, description="回调URL（预留）")
    priority: Optional[str] = Field(default="normal", description="优先级：high/normal/low")

    @field_validator("instruction")
    @classmethod
    def filter_instruction_secrets(cls, value: str) -> str:
        return _filter_sensitive(value)

    @field_validator("file_ids")
    @classmethod
    def enforce_single_primary_file(cls, value):
        if value and len(value) > 1:
            raise ValueError("当前 Office 任务只支持一个主文件，请一次提交一个文件")
        return value

    @field_validator("options", mode="before")
    @classmethod
    def filter_options_secrets(cls, value):
        return _filter_sensitive(value)


class FeedbackRequest(BaseModel):
    """任务反馈"""
    rating: int = Field(..., ge=1, le=5, description="评分1-5")
    comment: Optional[str] = Field(default=None, description="评价")
    tags: Optional[List[str]] = Field(default=None, description="标签")


class MultipartPartInfo(BaseModel):
    """分片信息"""
    part_number: int
    etag: str


class MultipartCompleteRequest(BaseModel):
    """完成分片上传请求"""
    parts: List[MultipartPartInfo] = Field(..., description="分片列表")
