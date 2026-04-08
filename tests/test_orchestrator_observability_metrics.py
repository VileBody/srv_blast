from __future__ import annotations

from services.orchestrator.observability_metrics import (
    get_counter_map,
    get_labeled_counter_samples,
    get_labeled_histogram_samples,
    increment_counter,
    increment_labeled_counter,
    observe_labeled_histogram,
)


class _FakeRedis:
    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, int]] = {}
        self._float_hashes: dict[str, dict[str, float]] = {}

    def hincrby(self, key: str, field: str, amount: int) -> int:
        bucket = self._hashes.setdefault(key, {})
        bucket[field] = int(bucket.get(field, 0)) + int(amount)
        return int(bucket[field])

    def hincrbyfloat(self, key: str, field: str, amount: float) -> float:
        bucket = self._float_hashes.setdefault(key, {})
        bucket[field] = float(bucket.get(field, 0.0)) + float(amount)
        return float(bucket[field])

    def hgetall(self, key: str) -> dict[str, str]:
        bucket_i = self._hashes.get(key, {})
        bucket_f = self._float_hashes.get(key, {})
        out = {k: str(v) for k, v in bucket_i.items()}
        out.update({k: str(v) for k, v in bucket_f.items()})
        return out


class _FakeStore:
    def __init__(self) -> None:
        self.key_prefix = "blast_test"
        self.r = _FakeRedis()

    def _redis_call(self, _op: str, fn):
        return fn()


def test_increment_and_read_counter_map() -> None:
    store = _FakeStore()
    assert increment_counter(store, metric="payment_webhook_outcomes", label="confirmed") == 1
    assert increment_counter(store, metric="payment_webhook_outcomes", label="confirmed") == 2
    assert increment_counter(store, metric="payment_webhook_outcomes", label="already_confirmed") == 1
    assert get_counter_map(store, metric="payment_webhook_outcomes") == {
        "confirmed": 2,
        "already_confirmed": 1,
    }


def test_metric_name_and_label_are_sanitized() -> None:
    store = _FakeStore()
    increment_counter(store, metric="Render Poll Timeout Outcomes", label="during status retry")
    assert get_counter_map(store, metric="Render Poll Timeout Outcomes") == {"during_status_retry": 1}


def test_labeled_counter_roundtrip() -> None:
    store = _FakeStore()
    increment_labeled_counter(
        store,
        metric="dispatch_attempt_total",
        labels={"node": "85.239.48.31", "api_mode": "render", "outcome": "accepted"},
    )
    increment_labeled_counter(
        store,
        metric="dispatch_attempt_total",
        labels={"node": "85.239.48.31", "api_mode": "render", "outcome": "accepted"},
    )
    samples = get_labeled_counter_samples(store, metric="dispatch_attempt_total")
    assert len(samples) == 1
    labels, count = samples[0]
    assert labels["node"] == "85.239.48.31"
    assert labels["api_mode"] == "render"
    assert labels["outcome"] == "accepted"
    assert count == 2


def test_labeled_histogram_roundtrip() -> None:
    store = _FakeStore()
    buckets = (1.0, 2.0, 5.0)
    observe_labeled_histogram(
        store,
        metric="gemini_latency_seconds",
        value=0.8,
        buckets=buckets,
        labels={"model": "gemini-3-pro-preview", "stage": "stage2_subtitles", "outcome": "success", "code_class": "ok"},
    )
    observe_labeled_histogram(
        store,
        metric="gemini_latency_seconds",
        value=3.0,
        buckets=buckets,
        labels={"model": "gemini-3-pro-preview", "stage": "stage2_subtitles", "outcome": "success", "code_class": "ok"},
    )

    samples = get_labeled_histogram_samples(store, metric="gemini_latency_seconds")
    assert len(samples) == 1
    sample = samples[0]
    assert int(sample["count"]) == 2
    assert float(sample["sum"]) == 3.8
    buckets_map = sample["buckets"]
    assert int(buckets_map[1.0]) == 1
    assert int(buckets_map[2.0]) == 1
    assert int(buckets_map[5.0]) == 2
