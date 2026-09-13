"""
响应数据模型
"""
from typing import Optional, List, Dict, Any, Generic, TypeVar
from pydantic import BaseModel, Field
from datetime import datetime, timezone


T = TypeVar("T")


class BaseResponse(BaseModel, Generic[T]):
    """统一响应格式"""
    success: bool = True
    data: Optional[T] = None
    error_code: Optional[str] = None
    message: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = False
    error_code: str
    message: str
    details: Optional[Any] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ChatResponse(BaseModel):
    """对话响应"""
    task_id: str
    conversation_id: Optional[str] = None
    agent: str
    intent: Optional[str] = None
    status: str = "running"
    message: str = "任务已创建"
    estimated_time: Optional[int] = None
    is_follow_up: bool = False
    parent_task_id: Optional[str] = None
    revision_mode: str = "new_task"
    revision_number: int = 1


class FileInfo(BaseModel):
    """文件信息"""
    file_id: str
    filename: str
    original_name: str
    file_type: str
    extension: str = ""
    size: int
    upload_time: str = ""
    deleted_at: Optional[str] = None
    version: int = 1
    status: str = "ready"
    metadata: Optional[Dict[str, Any]] = None


class FileUploadResponse(BaseModel):
    """文件上传响应"""
    file_id: str
    filename: str
    file_type: str
    size: int
    message: str = "上传成功"


class TaskFileInfo(BaseModel):
    """
    任务关联文件信息
    """
    file_id: str
    filename: str
    download_url: str

class TaskStep(BaseModel):
    """任务步骤"""
    step_id: str
    name: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    message: Optional[str] = None


class TaskInfo(BaseModel):
    """任务信息"""
    task_id: str
    task_type: str
    agent: str
    status: str
    progress: int = 0
    current_step: Optional[str] = None
    steps: List[TaskStep] = Field(default_factory=list)
    input_files: List[TaskFileInfo] = Field(default_factory=list)
    output_files: List[TaskFileInfo] = Field(default_factory=list)
    instruction: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    quality_score: Optional[float] = None
    parent_task_id: Optional[str] = None
    revision_number: int = 1
    storage: str = "persisted"
    degraded: bool = False


class TaskListResponse(BaseModel):
    """任务列表响应"""
    tasks: List[TaskInfo]
    total: int
    page: int
    page_size: int


class AgentInfo(BaseModel):
    """Agent信息"""
    agent_id: str
    name: str
    agent_type: str
    capabilities: List[str]
    description: str = ""
    status: str = "available"
    version: Optional[str] = None
    current_version: Optional[str] = None


class AgentListResponse(BaseModel):
    """Agent列表响应"""
    agents: List[AgentInfo]
    total: int
    # P5-15：True 表示数据库不可用、返回的是内置降级清单（不是用户配置的
    # agent 集合）。调用方可据此区分 requested / effective，不再把降级结果
    # 静默当作已配置结果。
    degraded: bool = False


class VersionInfo(BaseModel):
    """版本信息"""
    version: str
    name: str
    status: str
    created_at: str
    changelog: Optional[str] = None
