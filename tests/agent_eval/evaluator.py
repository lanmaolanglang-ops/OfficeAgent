"""
Agent Evaluation System
Agent 评估系统 - 自动评估各Agent输出质量
"""
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from pathlib import Path


class EvalType(str, Enum):
    """评估类型"""
    WORD = "word"
    PPT = "ppt"
    EXCEL = "excel"
    WORKFLOW = "workflow"


class EvalStatus(str, Enum):
    """评估状态"""
    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"
    ERROR = "error"


@dataclass
class EvalMetric:
    """评估指标"""
    name: str
    score: float  # 0-100
    weight: float = 1.0
    max_score: float = 100.0
    details: str = ""

    @property
    def weighted_score(self) -> float:
        return (self.score / self.max_score) * self.weight * 100


@dataclass
class EvalResult:
    """评估结果"""
    eval_type: EvalType
    test_name: str
    status: EvalStatus
    metrics: list[EvalMetric] = field(default_factory=list)
    duration: float = 0.0
    error: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def total_score(self) -> float:
        if not self.metrics:
            return 0.0
        total_weight = sum(m.weight for m in self.metrics)
        if total_weight == 0:
            return 0.0
        weighted_sum = sum(m.weighted_score for m in self.metrics)
        return weighted_sum / total_weight

    @property
    def passed(self) -> bool:
        return self.status == EvalStatus.PASS or (
            self.status == EvalStatus.PARTIAL and self.total_score >= 60
        )

    def to_dict(self) -> dict:
        return {
            "eval_type": self.eval_type.value,
            "test_name": self.test_name,
            "status": self.status.value,
            "total_score": round(self.total_score, 2),
            "duration": round(self.duration, 3),
            "metrics": [
                {"name": m.name, "score": m.score, "weight": m.weight, "details": m.details}
                for m in self.metrics
            ],
            "error": self.error,
        }


class BaseEvaluator:
    """评估器基类"""

    def __init__(self):
        self.results: list[EvalResult] = []

    def evaluate(self, *args, **kwargs) -> EvalResult:
        raise NotImplementedError

    def add_result(self, result: EvalResult):
        self.results.append(result)

    def get_summary(self) -> dict:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        avg_score = sum(r.total_score for r in self.results) / total if total else 0
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total * 100, 2) if total else 0,
            "avg_score": round(avg_score, 2),
        }
