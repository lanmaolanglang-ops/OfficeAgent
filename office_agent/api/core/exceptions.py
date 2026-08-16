"""
统一异常定义
"""
from typing import Optional, Any


class APIError(Exception):
    """API错误基类"""
    error_code: str = "API_ERROR"
    status_code: int = 500

    def __init__(self, message: str, error_code: str = None,
                 status_code: int = None, details: Any = None):
        self.message = message
        if error_code:
            self.error_code = error_code
        if status_code:
            self.status_code = status_code
        self.details = details
        super().__init__(message)


class AgentError(APIError):
    """Agent执行错误"""
    error_code = "AGENT_ERROR"
    status_code = 500


class AgentNotFoundError(APIError):
    """Agent不存在"""
    error_code = "AGENT_NOT_FOUND"
    status_code = 404


class FileError(APIError):
    """文件处理错误"""
    error_code = "FILE_ERROR"
    status_code = 400


class FileNotFoundAPIError(APIError):
    """文件不存在"""
    error_code = "FILE_NOT_FOUND"
    status_code = 404


class FileTypeNotSupportedError(APIError):
    """文件类型不支持"""
    error_code = "FILE_TYPE_NOT_SUPPORTED"
    status_code = 415


class TaskError(APIError):
    """任务错误"""
    error_code = "TASK_ERROR"
    status_code = 500


class TaskNotFoundError(APIError):
    """任务不存在"""
    error_code = "TASK_NOT_FOUND"
    status_code = 404


class TaskStateError(APIError):
    """任务状态错误"""
    error_code = "TASK_STATE_ERROR"
    status_code = 409


class ModelError(APIError):
    """模型调用错误"""
    error_code = "MODEL_ERROR"
    status_code = 502


class AuthError(APIError):
    """认证错误"""
    error_code = "AUTH_ERROR"
    status_code = 401


class PermissionDeniedError(APIError):
    """权限错误"""
    error_code = "PERMISSION_DENIED"
    status_code = 403


class ValidationError(APIError):
    """参数校验错误"""
    error_code = "VALIDATION_ERROR"
    status_code = 422


class RateLimitError(APIError):
    """限流错误"""
    error_code = "RATE_LIMITED"
    status_code = 429
