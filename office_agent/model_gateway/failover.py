"""
Failover Manager - 故障转移管理器
当模型调用失败时，自动切换到备用模型
"""
import time
from typing import Optional, Callable, Any

from ..models.model_schemas import ModelResponse, AITaskType
from .model_manager import ModelManager


class FailoverManager:
    """故障转移管理器"""
    
    def __init__(self, model_manager: ModelManager,
                 max_retries: int = 3,
                 retry_delay: float = 1.0):
        self.model_manager = model_manager
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._failure_history: dict[str, list[float]] = {}  # model_id -> 失败时间列表
        self._cooldown_seconds = 300  # 失败后冷却5分钟
    
    def execute_with_failover(self,
                              task_type: AITaskType,
                              action: Callable[[Any], ModelResponse],
                              model_ids: Optional[list[str]] = None,
                              cancel_event=None,
                              **kwargs) -> ModelResponse:
        """
        执行调用，失败时自动切换模型
        
        Args:
            task_type: 任务类型
            action: 执行函数，接收 client 参数，返回 ModelResponse
            model_ids: 指定模型ID列表（优先级），不指定则按路由
            **kwargs: 传递给 action 的额外参数
        """
        if self._is_cancelled(cancel_event):
            return self._cancelled_response(0, [])

        # 获取候选模型列表
        if model_ids is not None:
            candidates = model_ids
        else:
            candidates = self.model_manager.get_routing(task_type)
        
        # 过滤掉冷却中的模型
        available = []
        for mid in candidates:
            if not self._is_in_cooldown(mid):
                config = self.model_manager.get_model(mid)
                if config and config.enabled and config.api_key:
                    available.append(mid)
        
        if not available:
            configured = [
                mid for mid in candidates
                if (self.model_manager.get_model(mid)
                    and self.model_manager.get_model(mid).enabled
                    and self.model_manager.get_model(mid).api_key)
            ]
            message = (
                "所有可用模型均在冷却，请稍后重试"
                if configured else "没有可用的模型，请检查 API Key 配置"
            )
            return ModelResponse(
                success=False,
                error=message,
            )
        
        errors = []
        attempts = 0
        attempted_models = []
        
        for model_id in available:
            if self._is_cancelled(cancel_event):
                return self._cancelled_response(attempts, attempted_models)
            attempted_models.append(model_id)
            client = self.model_manager.get_client(model_id)
            if not client:
                errors.append(f"{model_id}: 客户端创建失败")
                continue
            
            # 尝试调用（支持重试）
            for attempt in range(self.max_retries):
                if self._is_cancelled(cancel_event):
                    return self._cancelled_response(attempts, attempted_models)
                try:
                    attempts += 1
                    result = action(client, **kwargs)
                    if self._is_cancelled(cancel_event):
                        return self._cancelled_response(attempts, attempted_models)
                    if result.success:
                        raw = dict(result.raw_response) if isinstance(result.raw_response, dict) else {}
                        raw["_office_agent"] = {
                            "attempts": attempts,
                            "attempted_models": attempted_models,
                            "fallback_used": model_id != candidates[0],
                        }
                        result.raw_response = raw
                        return result
                    else:
                        errors.append(f"{model_id} (attempt {attempt + 1}): {result.error}")
                        self._record_failure(model_id)
                        # 鉴权/参数等确定性错误重试不会恢复，直接切换备用模型。
                        if (attempt < self.max_retries - 1
                                and self._is_retryable_error(result.error)):
                            if self._wait_for_retry(cancel_event):
                                return self._cancelled_response(
                                    attempts, attempted_models
                                )
                            continue
                        break
                except Exception as e:
                    errors.append(f"{model_id} (尝试{attempt+1}): {str(e)}")
                    self._record_failure(model_id)
                    if (attempt < self.max_retries - 1
                            and self._is_retryable_error(str(e))):
                        if self._wait_for_retry(cancel_event):
                            return self._cancelled_response(
                                attempts, attempted_models
                            )
                        continue
                    break
        
        # 所有模型都失败了
        return ModelResponse(
            success=False,
            error="所有模型调用失败:\n" + "\n".join(errors[-5:]),
            raw_response={"_office_agent": {
                "attempts": attempts,
                "attempted_models": attempted_models,
                "fallback_used": len(attempted_models) > 1,
            }},
        )

    @staticmethod
    def _is_cancelled(cancel_event) -> bool:
        return bool(cancel_event is not None and cancel_event.is_set())

    def _wait_for_retry(self, cancel_event) -> bool:
        """Wait between retries, returning immediately when cancellation wins."""
        if self.retry_delay <= 0:
            return self._is_cancelled(cancel_event)
        if cancel_event is not None:
            return bool(cancel_event.wait(self.retry_delay))
        time.sleep(self.retry_delay)
        return False

    @staticmethod
    def _cancelled_response(attempts: int,
                            attempted_models: list[str]) -> ModelResponse:
        return ModelResponse(
            success=False,
            error="任务已取消",
            raw_response={"_office_agent": {
                "attempts": attempts,
                "attempted_models": list(attempted_models),
                "fallback_used": len(attempted_models) > 1,
                "cancelled": True,
            }},
        )

    @staticmethod
    def _is_retryable_error(error: str) -> bool:
        """仅对可能自行恢复的网络、限流和服务端错误重试。"""
        text = (error or "").lower()
        permanent = (
            "http 400", "http 401", "http 403", "http 404",
            "authentication", "api key", "invalid_request", "invalid api",
            "鉴权", "认证失败",
        )
        if any(marker in text for marker in permanent):
            return False
        transient = (
            "http 408", "http 409", "http 425", "http 429", "http 5",
            "timeout", "timed out", "connection", "连接错误", "连接超时",
            "temporar", "rate limit", "限流", "服务繁忙",
        )
        return any(marker in text for marker in transient) or not text
    
    def _record_failure(self, model_id: str):
        """记录模型失败"""
        now = time.time()
        if model_id not in self._failure_history:
            self._failure_history[model_id] = []
        self._failure_history[model_id].append(now)
        
        # 只保留最近1小时的记录
        self._failure_history[model_id] = [
            t for t in self._failure_history[model_id]
            if now - t < 3600
        ]
    
    def _is_in_cooldown(self, model_id: str) -> bool:
        """检查模型是否在冷却中（最近5分钟内失败过3次以上）"""
        if model_id not in self._failure_history:
            return False
        now = time.time()
        recent_failures = [
            t for t in self._failure_history[model_id]
            if now - t < self._cooldown_seconds
        ]
        return len(recent_failures) >= 3
    
    def get_model_status(self) -> dict[str, dict]:
        """获取所有模型状态"""
        status = {}
        for model in self.model_manager.list_models():
            mid = model.id
            failures = self._failure_history.get(mid, [])
            status[mid] = {
                "name": model.display_name,
                "enabled": model.enabled,
                "has_key": bool(model.api_key),
                "recent_failures": len(failures),
                "in_cooldown": self._is_in_cooldown(mid),
            }
        return status
