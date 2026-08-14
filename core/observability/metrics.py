"""指标收集（observability.md §1）：计数 + 延迟样本分位数，线程安全。"""
import threading
from collections import deque


class MetricsCollector:
    """进程内指标：counter + 延迟样本（保留最近 N 个，P50/P95/P99 即时计算）。"""

    def __init__(self, sample_capacity: int = 1000) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._samples: dict[str, deque[float]] = {}
        self._capacity = sample_capacity

    def incr(self, name: str, delta: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + delta

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            samples = self._samples.setdefault(name, deque(maxlen=self._capacity))
            samples.append(value)

    def _percentile(self, samples: list[float], pct: float) -> float | None:
        if not samples:
            return None
        ordered = sorted(samples)
        idx = min(len(ordered) - 1, int(pct / 100.0 * len(ordered)))
        return ordered[idx]

    def counter(self, name: str) -> int:
        with self._lock:
            return self._counters.get(name, 0)

    def snapshot(self) -> dict:
        """端点输出形态：counters + 每延迟指标的 n/P50/P95/P99。"""
        with self._lock:
            counters = dict(self._counters)
            samples = {k: list(v) for k, v in self._samples.items()}
        result: dict = {"counters": counters, "latencies": {}}
        for name, values in samples.items():
            result["latencies"][name] = {
                "n": len(values),
                "p50": self._percentile(values, 50),
                "p95": self._percentile(values, 95),
                "p99": self._percentile(values, 99),
            }
        return result
