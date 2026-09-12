"""
指标收集系统

内存级指标收集，支持 Prometheus 格式导出。
不依赖 prometheus_client 包，纯 Python 实现。

指标类型：
- Counter: 只增不减
- Gauge: 可增可减
- Histogram: 分布统计
"""
import time
import threading
from collections import defaultdict
from typing import Dict, List, Tuple


def _escape_label_value(value) -> str:
    """按 Prometheus 文本格式转义 label 值。

    值里出现反斜杠/双引号/换行会让 exposition 解析失败（agent 名等
    来自运行时数据，不能假设安全）。
    """
    return (str(value).replace("\\", "\\\\")
            .replace('"', '\\"').replace("\n", "\\n"))


class Metric:
    """指标基类"""
    def __init__(self, name: str, description: str = "", labels: List[str] | None = None):
        self.name = name
        self.description = description
        self.labels = labels or []
        self._lock = threading.Lock()


class Counter(Metric):
    """计数器"""
    def __init__(self, name: str, description: str = "", labels: List[str] | None = None):
        super().__init__(name, description, labels)
        self._values: Dict[tuple, float] = defaultdict(float)

    def inc(self, amount: float = 1, **labels):
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._values[key] += amount

    def get(self, **labels) -> float:
        key = tuple(sorted(labels.items())) if labels else ()
        return self._values.get(key, 0)

    def collect(self) -> List[Tuple[dict, float]]:
        with self._lock:
            return [(dict(k), v) for k, v in self._values.items()]


class Gauge(Metric):
    """仪表盘"""
    def __init__(self, name: str, description: str = "", labels: List[str] | None = None):
        super().__init__(name, description, labels)
        self._values: Dict[tuple, float] = defaultdict(float)

    def set(self, value: float, **labels):
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._values[key] = value

    def inc(self, amount: float = 1, **labels):
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._values[key] += amount

    def dec(self, amount: float = 1, **labels):
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._values[key] -= amount

    def get(self, **labels) -> float:
        key = tuple(sorted(labels.items())) if labels else ()
        return self._values.get(key, 0)

    def collect(self) -> List[Tuple[dict, float]]:
        with self._lock:
            return [(dict(k), v) for k, v in self._values.items()]


class Histogram(Metric):
    """直方图（分布统计）"""
    DEFAULT_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300]

    def __init__(self, name: str, description: str = "",
                 buckets: List[float] | None = None, labels: List[str] | None = None):
        super().__init__(name, description, labels)
        self.buckets = sorted(buckets or self.DEFAULT_BUCKETS)
        self._counts: Dict[tuple, List[int]] = defaultdict(
            lambda: [0] * (len(self.buckets) + 1)
        )
        self._sums: Dict[tuple, float] = defaultdict(float)
        self._total: Dict[tuple, int] = defaultdict(int)

    def observe(self, value: float, **labels):
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            for i, bucket in enumerate(self.buckets):
                if value <= bucket:
                    self._counts[key][i] += 1
                    break
            else:
                self._counts[key][-1] += 1
            self._sums[key] += value
            self._total[key] += 1

    def collect(self) -> List[Tuple[dict, dict]]:
        with self._lock:
            results = []
            for key, counts in self._counts.items():
                # 累积桶
                cumulative = []
                acc = 0
                for c in counts:
                    acc += c
                    cumulative.append(acc)
                results.append((
                    dict(key),
                    {
                        "buckets": self.buckets,
                        "counts": cumulative,
                        "sum": self._sums[key],
                        "total": self._total[key],
                    }
                ))
            return results


class MetricsRegistry:
    """
    指标注册表（单例）

    用法：
        from office_agent.logging_system.metrics import registry

        registry.counter("api_requests_total").inc(method="GET", path="/api/health")
        registry.histogram("api_request_duration_seconds").observe(0.123, path="/api/chat")
    """

    def __init__(self):
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, description: str = "", labels: List[str] | None = None) -> Counter:
        with self._lock:
            if name not in self._counters:
                self._counters[name] = Counter(name, description, labels)
            return self._counters[name]

    def gauge(self, name: str, description: str = "", labels: List[str] | None = None) -> Gauge:
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = Gauge(name, description, labels)
            return self._gauges[name]

    def histogram(self, name: str, description: str = "",
                  buckets: List[float] | None = None, labels: List[str] | None = None) -> Histogram:
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = Histogram(name, description, buckets, labels)
            return self._histograms[name]

    def render_prometheus(self) -> str:
        """导出 Prometheus 文本格式"""
        lines = []
        with self._lock:
            counters = list(self._counters.values())
            gauges = list(self._gauges.values())
            histograms = list(self._histograms.values())

        # Counters
        for counter in counters:
            if counter.description:
                lines.append(f"# HELP {counter.name} {counter.description}")
            lines.append(f"# TYPE {counter.name} counter")
            for labels, value in counter.collect():
                label_str = ",".join(
                    f'{k}="{_escape_label_value(v)}"' for k, v in labels.items())
                if label_str:
                    lines.append(f"{counter.name}{{{label_str}}} {value}")
                else:
                    lines.append(f"{counter.name} {value}")

        # Gauges
        for gauge in gauges:
            if gauge.description:
                lines.append(f"# HELP {gauge.name} {gauge.description}")
            lines.append(f"# TYPE {gauge.name} gauge")
            for labels, value in gauge.collect():
                label_str = ",".join(
                    f'{k}="{_escape_label_value(v)}"' for k, v in labels.items())
                if label_str:
                    lines.append(f"{gauge.name}{{{label_str}}} {value}")
                else:
                    lines.append(f"{gauge.name} {value}")

        # Histograms
        for hist in histograms:
            if hist.description:
                lines.append(f"# HELP {hist.name} {hist.description}")
            lines.append(f"# TYPE {hist.name} histogram")
            for labels, data in hist.collect():
                label_str = ",".join(
                    f'{k}="{_escape_label_value(v)}"' for k, v in labels.items())
                for i, bucket in enumerate(hist.buckets):
                    le = f'le="{bucket}"'
                    full_label = f"{label_str},{le}" if label_str else le
                    lines.append(f"{hist.name}_bucket{{{full_label}}} {data['counts'][i]}")
                inf_label = f'{label_str},le="+Inf"' if label_str else 'le="+Inf"'
                lines.append(f"{hist.name}_bucket{{{inf_label}}} {data['total']}")
                lines.append(f"{hist.name}_sum {data['sum']}")
                lines.append(f"{hist.name}_count {data['total']}")

        return "\n".join(lines) + "\n"

    def get_summary(self) -> dict:
        """获取指标摘要（JSON格式）"""
        result: dict = {"counters": {}, "gauges": {}, "histograms": {}}
        with self._lock:
            counters = list(self._counters.items())
            gauges = list(self._gauges.items())
            histograms = list(self._histograms.items())

        for name, counter in counters:
            result["counters"][name] = {
                "description": counter.description,
                "values": [{"labels": label, "value": v} for label, v in counter.collect()],
            }

        for name, gauge in gauges:
            result["gauges"][name] = {
                "description": gauge.description,
                "values": [{"labels": label, "value": v} for label, v in gauge.collect()],
            }

        for name, hist in histograms:
            result["histograms"][name] = {
                "description": hist.description,
                "values": [{"labels": label, **d} for label, d in hist.collect()],
            }

        return result


# 全局单例
registry = MetricsRegistry()

# 预定义常用指标
def init_default_metrics():
    """初始化默认指标"""
    # API
    registry.counter("api_requests_total", "Total API requests", ["method", "path", "status"])
    registry.histogram("api_request_duration_seconds", "API request duration",
                       labels=["method", "path"])
    registry.counter("api_errors_total", "Total API errors", ["method", "path", "error_type"])

    # Tasks
    registry.counter("tasks_total", "Total tasks", ["task_type", "status"])
    registry.histogram("task_duration_seconds", "Task duration", labels=["task_type"])
    registry.gauge("tasks_active", "Active tasks", ["task_type"])

    # Agent
    registry.counter("agent_executions_total", "Agent executions", ["agent", "action", "status"])
    registry.histogram("agent_duration_seconds", "Agent execution duration", labels=["agent"])

    # Model
    registry.counter("model_calls_total", "Model calls", ["model", "provider", "status"])
    registry.histogram("model_call_duration_seconds", "Model call latency", labels=["model"])
    registry.counter("model_tokens_total", "Total tokens", ["model", "type"])
    registry.counter("model_cost_total", "Total cost", ["model"])

    # Files
    registry.counter("files_processed_total", "Files processed", ["file_type", "action"])
    registry.counter("file_uploads_total", "File uploads", ["file_type"])

    # System
    registry.gauge("system_active_requests", "Active requests")
    registry.counter("system_errors_total", "System errors", ["error_type"])


init_default_metrics()


class Timer:
    """计时器（配合 Histogram 使用）"""
    def __init__(self, histogram: Histogram, **labels):
        self.histogram = histogram
        self.labels = labels
        self.start = 0

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        duration = time.time() - self.start
        self.histogram.observe(duration, **self.labels)
        return False
