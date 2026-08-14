"""可观测性测试（计数器/分位数/白名单脱敏/trace_id 中间件）。"""
import pytest

from core.observability.metrics import MetricsCollector


def test_counter_increment():
    m = MetricsCollector()
    m.incr("search.requests", 2)
    m.incr("search.requests")
    assert m.counter("search.requests") == 3


def test_latency_percentiles():
    m = MetricsCollector()
    for v in range(1, 101):  # 1..100
        m.observe("search.latency_ms", float(v))
    snap = m.snapshot()["latencies"]["search.latency_ms"]
    assert snap["n"] == 100
    assert snap["p50"] == pytest.approx(51)
    assert snap["p95"] == pytest.approx(96)
    assert snap["p99"] == pytest.approx(100)


def test_sample_capacity_bounded():
    m = MetricsCollector(sample_capacity=10)
    for v in range(100):
        m.observe("x", float(v))
    snap = m.snapshot()["latencies"]["x"]
    assert snap["n"] == 10  # 只保留最近 10 个样本


def test_empty_latency_returns_none():
    snap = MetricsCollector().snapshot()
    assert snap["counters"] == {}
    assert snap["latencies"] == {}


def test_log_field_whitelist_filters_sensitive_keys():
    from core.observability.logging import _filter_fields

    filtered = _filter_fields(None, None, {
        "event": "ok",
        "trace_id": "t1",
        "password": "secret",  # 未登记字段 → 丢弃
        "query_text": "用户查询",  # 未登记 → 丢弃
        "duration_ms": 12,
    })
    assert filtered == {"event": "ok", "trace_id": "t1", "duration_ms": 12}
