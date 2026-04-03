from __future__ import annotations

from services.orchestrator.observability_metrics import get_counter_map, increment_counter


class _FakeRedis:
    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, int]] = {}

    def hincrby(self, key: str, field: str, amount: int) -> int:
        bucket = self._hashes.setdefault(key, {})
        bucket[field] = int(bucket.get(field, 0)) + int(amount)
        return int(bucket[field])

    def hgetall(self, key: str) -> dict[str, str]:
        bucket = self._hashes.get(key, {})
        return {k: str(v) for k, v in bucket.items()}


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
