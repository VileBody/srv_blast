from __future__ import annotations

from services.orchestrator.prometheus_metrics import build_prometheus_metrics_payload
from services.orchestrator.observability_metrics import (
    increment_labeled_counter,
    observe_labeled_histogram,
)
from services.orchestrator.schemas import JobState


class _FakeRedis:
    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, int]] = {}
        self._float_hashes: dict[str, dict[str, float]] = {}
        self._values: dict[str, str] = {}

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

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def set(self, key: str, value: str) -> bool:
        self._values[key] = value
        return True


class _FakeStore:
    def __init__(self) -> None:
        self.key_prefix = "blast_test"
        self.r = _FakeRedis()
        self._jobs: list[JobState] = []

    def _redis_call(self, _op: str, fn):
        return fn()

    def list_jobs(self):
        return list(self._jobs)


def _mk_job(job_id: str, status: str, *, stage: str | None = None) -> JobState:
    return JobState(
        job_id=job_id,
        status=status,  # type: ignore[arg-type]
        version=1,
        created_at=1.0,
        updated_at=2.0,
        queued_at=1.1,
        started_at=1.2,
        finished_at=None,
        stage=stage,
        idempotency_key=None,
        request={},
        result={},
        error=None,
    )


def test_prometheus_payload_contains_new_observability_metrics() -> None:
    store = _FakeStore()
    store._jobs = [
        _mk_job("j1", "QUEUED"),
        _mk_job("j2", "RUNNING", stage="dispatch"),
        _mk_job("j3", "FAILED", stage="poll"),
    ]

    increment_labeled_counter(
        store,
        metric="dispatch_attempt_total",
        labels={"node": "85.239.48.31", "api_mode": "render", "outcome": "accepted"},
    )
    increment_labeled_counter(
        store,
        metric="rust_gen_dispatch_total",
        labels={"engine": "rust_gen", "subtitle_mode": "brat_5th", "outcome": "accepted"},
    )
    increment_labeled_counter(
        store,
        metric="gemini_call_total",
        labels={
            "model": "gemini-3-pro-preview",
            "stage": "stage2_subtitles",
            "outcome": "error",
            "code_class": "503",
        },
    )
    increment_labeled_counter(
        store,
        metric="gemini_token_total",
        labels={
            "provider": "vertex",
            "model": "gemini-3-pro-preview",
            "stage": "stage2_subtitles",
            "token_type": "total",
        },
        amount=1234,
    )
    observe_labeled_histogram(
        store,
        metric="gemini_latency_seconds",
        value=3.2,
        buckets=(1.0, 2.0, 5.0),
        labels={
            "model": "gemini-3-pro-preview",
            "stage": "stage2_subtitles",
            "outcome": "success",
            "code_class": "ok",
        },
    )

    payload, content_type = build_prometheus_metrics_payload(store)
    body = payload.decode("utf-8")

    assert "text/plain" in content_type
    assert "queue_depth" in body
    assert "inflight_jobs" in body
    assert "failed_jobs" in body
    assert "render_backlog" in body
    assert "build_backlog" in body
    assert "job_stage_count" in body
    assert "llm_worker_max_inflight" in body
    assert "llm_worker_available_slots" in body
    assert "llm_worker_saturated" in body
    assert "backpressure_policy_state" in body
    assert "render_poll_split_active" in body
    assert 'job_stage_count{stage="dispatch"}' in body
    assert "capacity_policy_state" in body
    assert "runtime_config_numeric_value" in body
    assert "dispatch_attempt_total" in body
    assert "rust_gen_dispatch_total" in body
    assert "gemini_call_total" in body
    assert "gemini_token_total" in body
    assert "gemini_latency_seconds_bucket" in body
