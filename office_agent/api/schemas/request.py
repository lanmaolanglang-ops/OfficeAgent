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

    @field_validator("message")
    @classmethod
    def filter_message_secrets(cls, value: str) -> str:
        return _filter_sensitive(value)

    @field_validator("context", mode="before")
    @classmethod
    def filter_context_secrets(cls, value):
        return _filter_sensitive(value)


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

    @field_validator("options", mode="before")
    @classmethod
    def filter_options_secrets(cls, value):
        return _filter_sensitive(value)


class TaskListRequest(BaseModel):
    """任务列表查询"""
    status: Optional[str] = Field(default=None, description="按状态筛选")
    agent: Optional[str] = Field(default=None, description="按Agent筛选")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class AgentConfigRequest(BaseModel):
    """Agent配置更新"""
    config: Dict[str, Any] = Field(..., description="配置项")


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
