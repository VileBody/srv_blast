from __future__ import annotations

from services.orchestrator.windows_dispatch_priority import (
    WindowsDispatchPriorityGate,
    normalize_windows_dispatch_priority,
)
from services.orchestrator.schemas import RequeueJobRequest, SendAudioS3Request
from services.orchestrator import celery_app as celery_module


class _FakeRedis:
    def __init__(self) -> None:
        self._zsets: dict[str, dict[str, float]] = {}

    def zadd(self, key: str, mapping: dict[str, float], nx: bool = False) -> int:
        bucket = self._zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if nx and member in bucket:
                continue
            added += int(member not in bucket)
            bucket[member] = float(score)
        return added

    def zrem(self, key: str, *members: str) -> int:
        bucket = self._zsets.setdefault(key, {})
        removed = 0
        for member in members:
            removed += int(bucket.pop(member, None) is not None)
        return removed

    def zrange(self, key: str, start: int, end: int) -> list[str]:
        rows = sorted(self._zsets.get(key, {}).items(), key=lambda row: (row[1], row[0]))
        stop = len(rows) if end == -1 else end + 1
        return [member for member, _score in rows[start:stop]]

    def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))


def test_live_jobs_run_before_older_bulk_jobs_and_each_class_is_fifo() -> None:
    gate = WindowsDispatchPriorityGate(redis_client=_FakeRedis(), key_prefix="test")
    active = {"bulk-old", "bulk-new", "live-old", "live-new"}

    gate.register("bulk-new", priority="bulk", enqueued_at=20.0)
    gate.register("bulk-old", priority="bulk", enqueued_at=10.0)
    gate.register("live-new", priority="live", enqueued_at=40.0)
    gate.register("live-old", priority="live", enqueued_at=30.0)

    is_active = active.__contains__
    assert gate.current_turn(is_active=is_active) == ("live-old", "live")
    assert gate.is_turn("bulk-old", is_active=is_active) == (
        False,
        "live-old",
        "live",
    )

    gate.remove("live-old")
    assert gate.current_turn(is_active=is_active) == ("live-new", "live")
    gate.remove("live-new")
    assert gate.current_turn(is_active=is_active) == ("bulk-old", "bulk")
    assert gate.counts() == {"live": 0, "bulk": 2}


def test_stale_jobs_are_pruned_and_priority_update_moves_job() -> None:
    redis = _FakeRedis()
    gate = WindowsDispatchPriorityGate(redis_client=redis, key_prefix="test")
    gate.register("stale-live", priority="live", enqueued_at=1.0)
    gate.register("job", priority="live", enqueued_at=2.0)
    gate.register("job", priority="bulk", enqueued_at=2.0)

    assert gate.current_turn(is_active=lambda jid: jid == "job") == ("job", "bulk")
    assert gate.counts() == {"live": 0, "bulk": 1}


def test_default_priority_is_live_and_invalid_values_fail_explicitly() -> None:
    assert normalize_windows_dispatch_priority(None) == "live"
    try:
        normalize_windows_dispatch_priority("urgent")
    except ValueError as exc:
        assert "unsupported windows dispatch priority" in str(exc)
    else:
        raise AssertionError("invalid priority must fail")


def test_api_jobs_default_live_while_admin_requeues_default_bulk() -> None:
    request = SendAudioS3Request(audio_s3_url="s3://bucket/audio.mp3")
    assert request.render_priority == "live"
    assert RequeueJobRequest().render_priority == "bulk"


def test_retry_state_stays_queued_for_dispatch_admission_waits(monkeypatch) -> None:
    class _Store:
        def __init__(self) -> None:
            self.state = type("State", (), {"status": "RUNNING"})()
            self.last = None

        def get(self, _job_id: str):
            return self.state

        def set_status(self, job_id, status, *, stage=None, error=None):
            self.last = (job_id, status, stage, error)

    store = _Store()
    monkeypatch.setattr(
        celery_module.JobStore,
        "from_env",
        classmethod(lambda cls: store),
    )
    task = celery_module.JobBoundTask()

    task._set_retrying(
        "job",
        error="celery_retry exc=windows_dispatch_priority_wait",
    )
    assert store.last[:3] == ("job", "QUEUED", "render_wait_priority")

    task._set_retrying(
        "job",
        error="celery_retry exc=windows_node_pool_saturated",
    )
    assert store.last[:3] == ("job", "QUEUED", "render_wait_capacity")
