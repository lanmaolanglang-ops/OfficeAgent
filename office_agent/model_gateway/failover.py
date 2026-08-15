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
                              **kwargs) -> ModelResponse:
        """
        执行调用，失败时自动切换模型
        
        Args:
            task_type: 任务类型
            action: 执行函数，接收 client 参数，返回 ModelResponse
            model_ids: 指定模型ID列表（优先级），不指定则按路由
            **kwargs: 传递给 action 的额外参数
        """
        # 获取候选模型列表
        if model_ids:
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
            # 所有模型都在冷却，重置冷却再试
            self._failure_history.clear()
            available = [mid for mid in candidates
                        if self.model_manager.get_model(mid) and
                        self.model_manager.get_model(mid).api_key]
        
        if not available:
            return ModelResponse(
                success=False,
                error="没有可用的模型，请检查 API Key 配置",
            )
        
        errors = []
        attempts = 0
        
        for model_id in available:
            client = self.model_manager.get_client(model_id)
            if not client:
                errors.append(f"{model_id}: 客户端创建失败")
                continue
            
            # 尝试调用（支持重试）
            for attempt in range(self.max_retries):
                try:
                    attempts += 1
                    result = action(client, **kwargs)
                    if result.success:
                        # 成功，记录并返回
                        return result
                    else:
                        errors.append(f"{model_id} (attempt {attempt + 1}): {result.error}")
                        # 非网络错误，不重试，直接切换下一个模型
                        if attempt < self.max_retries - 1:
                            time.sleep(self.retry_delay)
                            continue
                except Exception as e:
                    errors.append(f"{model_id} (尝试{attempt+1}): {str(e)}")
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay)
            
            # 记录失败
            self._record_failure(model_id)
        
        # 所有模型都失败了
        return ModelResponse(
            success=False,
            error=f"所有模型调用失败:\n" + "\n".join(errors[-5:]),
        )
    
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
