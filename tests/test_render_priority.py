from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.orchestrator import render_priority, tasks


class _FakeZsetRedis:
    """Minimal Redis stand-in covering the sorted-set calls the registry makes."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, float]] = {}
        self.expires: dict[str, int] = {}

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        bucket = self.data.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in bucket:
                added += 1
            bucket[member] = float(score)
        return added

    def zrem(self, key: str, member: str) -> int:
        bucket = self.data.get(key) or {}
        return 1 if bucket.pop(member, None) is not None else 0

    def zremrangebyscore(self, key: str, low: Any, high: Any) -> int:
        bucket = self.data.get(key) or {}
        low_v = float("-inf") if low in {"-inf", b"-inf"} else float(low)
        high_v = float("inf") if high in {"+inf", b"+inf"} else float(high)
        doomed = [m for m, s in bucket.items() if low_v <= s <= high_v]
        for member in doomed:
            bucket.pop(member, None)
        return len(doomed)

    def zcard(self, key: str) -> int:
        return len(self.data.get(key) or {})

    def zrange(self, key: str, start: int, stop: int) -> list[str]:
        bucket = self.data.get(key) or {}
        ordered = [m for m, _ in sorted(bucket.items(), key=lambda kv: kv[1])]
        if stop < 0:
            return ordered[start:]
        return ordered[start : stop + 1]

    def expire(self, key: str, ttl: int) -> bool:
        self.expires[key] = int(ttl)
        return True


class _BrokenRedis:
    def __getattr__(self, name: str):
        def _raise(*args: Any, **kwargs: Any):
            raise RuntimeError("redis down: " + name)

        return _raise


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


def test_classify_uses_explicit_job_priority_over_key() -> None:
    req = {"job_priority": "batch", "idempotency_key": "tg-42-abc-v1"}
    assert render_priority.classify_job(req) == render_priority.CLASS_BATCH


def test_classify_bot_jobs_as_interactive() -> None:
    req = {"idempotency_key": "tg-1882692780-batch-tg-1882692780-8c100d035656-v1"}
    assert render_priority.classify_job(req) == render_priority.CLASS_INTERACTIVE


def test_classify_operator_batch_as_batch() -> None:
    req = {"idempotency_key": "lilmac-10x100-20260821-toxin-v071"}
    assert render_priority.classify_job(req) == render_priority.CLASS_BATCH


def test_classify_unknown_shape_defaults_to_interactive() -> None:
    # Delaying a batch job costs throughput; delaying a user costs a user.
    assert render_priority.classify_job({}) == render_priority.CLASS_INTERACTIVE
    assert render_priority.classify_job(None) == render_priority.CLASS_INTERACTIVE


def test_classify_ignores_unknown_priority_value() -> None:
    req = {"job_priority": "urgent", "idempotency_key": "lilmac-x-v1"}
    assert render_priority.classify_job(req) == render_priority.CLASS_BATCH


# ---------------------------------------------------------------------------
# waiting registry
# ---------------------------------------------------------------------------


def test_mark_and_clear_waiting_roundtrip() -> None:
    r = _FakeZsetRedis()
    render_priority.mark_waiting(r, key_prefix="blast", job_id="job-1", now=1000.0)
    assert render_priority.interactive_waiting(r, key_prefix="blast", now=1000.0) == 1
    assert render_priority.waiting_job_ids(r, key_prefix="blast") == ("job-1",)

    render_priority.clear_waiting(r, key_prefix="blast", job_id="job-1")
    assert render_priority.interactive_waiting(r, key_prefix="blast", now=1000.0) == 0


def test_expired_waiter_stops_blocking_the_batch() -> None:
    r = _FakeZsetRedis()
    render_priority.mark_waiting(r, key_prefix="blast", job_id="job-1", ttl_s=300, now=1000.0)
    assert render_priority.interactive_waiting(r, key_prefix="blast", now=1200.0) == 1
    # Past the deadline the registration is pruned, so a crashed waiter cannot
    # starve the batch forever.
    assert render_priority.interactive_waiting(r, key_prefix="blast", now=1400.0) == 0


def test_registry_fails_open_when_redis_is_down() -> None:
    r = _BrokenRedis()
    render_priority.mark_waiting(r, key_prefix="blast", job_id="job-1")
    render_priority.clear_waiting(r, key_prefix="blast", job_id="job-1")
    assert render_priority.interactive_waiting(r, key_prefix="blast") == 0
    assert render_priority.waiting_job_ids(r, key_prefix="blast") == ()


def test_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RENDER_PRIORITY_ENABLED", raising=False)
    assert render_priority.priority_enabled() is True
    monkeypatch.setenv("RENDER_PRIORITY_ENABLED", "0")
    assert render_priority.priority_enabled() is False


def test_backoff_windows_favour_interactive() -> None:
    i_base, i_cap = render_priority.interactive_backoff_s()
    b_base, b_cap = render_priority.batch_defer_backoff_s()
    assert i_base < b_base and i_cap < b_cap


# ---------------------------------------------------------------------------
# dispatch gate
# ---------------------------------------------------------------------------


class _Store:
    def __init__(self, *, job_id: str, request: dict[str, Any], redis: Any) -> None:
        self.key_prefix = "blast_test"
        self.r = redis
        self._job_id = job_id
        self._state = SimpleNamespace(
            job_id=job_id,
            request=dict(request),
            status="NEW",
            stage=None,
            result=None,
            error=None,
        )

    def get(self, job_id: str):
        return self._state if job_id == self._job_id else None

    def set_status(self, job_id: str, status: str, *, stage=None, error=None, result=None):
        self._state.status = status
        if stage is not None:
            self._state.stage = stage
        if result is not None:
            base = dict(self._state.result or {})
            base.update(result)
            self._state.result = base
        return self._state


class _SaturatedPool:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)

    def get_active_urls(self, default_urls=None):
        _ = default_urls
        return ["http://win-node:8000"]

    def reserve_best(self, remaining):
        raise tasks.WindowsNodePoolSaturated(urls=remaining, max_inflight_per_node=2)

    def inflight_snapshot(self, urls):
        return {str(u): 2 for u in urls}

    def release(self, _url: str) -> None:
        return None


class _RetryRaised(RuntimeError):
    pass


def _setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, store: _Store) -> dict[str, Any]:
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    jsx = out_dir / "render_full.jsx"
    payload = out_dir / "final_render_instructions_full.json"
    jsx.write_text("// jsx", encoding="utf-8")
    payload.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(tasks.JobStore, "from_env", classmethod(lambda cls: store))
    monkeypatch.setattr(tasks, "WindowsNodePool", _SaturatedPool)
    monkeypatch.setattr(
        tasks,
        "make_job_paths",
        lambda **kwargs: SimpleNamespace(render_jsx=jsx, render_payload=payload),
    )
    monkeypatch.setattr(tasks, "_windows_default_urls", lambda: ["http://win-node:8000"])
    monkeypatch.setattr(
        tasks, "build_windows_job_payload", lambda **kwargs: {"job_id": kwargs["job_id"]}
    )
    monkeypatch.setattr(
        tasks,
        "SETTINGS",
        SimpleNamespace(
            work_dir="/tmp/work",
            output_dir="/tmp/output",
            windows_node_lease_ttl_s=7200,
            windows_node_max_inflight=2,
            windows_dispatch_max_retries=30,
            windows_timeout_s=300.0,
            windows_poll_interval_s=2.0,
            windows_render_api_mode="render",
            celery_queue_render="render",
            celery_queue_render_poll="render_poll",
        ),
    )

    class _NeverCalled:
        def __init__(self, *a: Any, **k: Any) -> None:
            raise AssertionError("windows client must not be called while waiting")

    monkeypatch.setattr(tasks, "WindowsRenderClient", _NeverCalled)

    retry_call: dict[str, Any] = {}

    def _retry(**kwargs: Any):
        retry_call.update(kwargs)
        raise _RetryRaised(str(kwargs.get("exc")))

    monkeypatch.setattr(tasks.dispatch_to_windows, "retry", _retry)
    return retry_call


def test_batch_job_yields_while_a_user_job_waits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    redis = _FakeZsetRedis()
    store = _Store(
        job_id="batch-1",
        request={"audio_s3_url": "s3://b/a.mp3", "idempotency_key": "lilmac-batch-v7"},
        redis=redis,
    )
    retry_call = _setup(monkeypatch, tmp_path, store)
    render_priority.mark_waiting(redis, key_prefix="blast_test", job_id="user-9")

    with pytest.raises(_RetryRaised, match="render_priority_deferred"):
        tasks.dispatch_to_windows.run("batch-1")

    st = store.get("batch-1")
    assert st.stage == "render_wait_priority"
    assert st.result["render_priority"]["interactive_waiting"] == 1
    # It steps aside briefly, so throughput returns as soon as the user is served.
    assert retry_call["countdown"] <= 30.0


def test_batch_job_proceeds_when_no_user_is_waiting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    redis = _FakeZsetRedis()
    store = _Store(
        job_id="batch-2",
        request={"audio_s3_url": "s3://b/a.mp3", "idempotency_key": "lilmac-batch-v8"},
        redis=redis,
    )
    _setup(monkeypatch, tmp_path, store)

    # Nobody waiting -> the batch reaches the pool and hits ordinary saturation.
    with pytest.raises(_RetryRaised, match="windows_node_pool_saturated"):
        tasks.dispatch_to_windows.run("batch-2")

    assert store.get("batch-2").stage == "render_wait_capacity"


def test_user_job_registers_itself_and_backs_off_briefly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    redis = _FakeZsetRedis()
    store = _Store(
        job_id="user-1",
        request={"audio_s3_url": "s3://b/a.mp3", "idempotency_key": "tg-777-abc-v1"},
        redis=redis,
    )
    retry_call = _setup(monkeypatch, tmp_path, store)

    with pytest.raises(_RetryRaised, match="windows_node_pool_saturated"):
        tasks.dispatch_to_windows.run("user-1")

    assert render_priority.interactive_waiting(redis, key_prefix="blast_test") == 1
    assert render_priority.waiting_job_ids(redis, key_prefix="blast_test") == ("user-1",)
    # Short window so the user grabs the very next freed slot.
    assert retry_call["countdown"] <= 15.0


def test_kill_switch_restores_plain_lottery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RENDER_PRIORITY_ENABLED", "0")
    redis = _FakeZsetRedis()
    store = _Store(
        job_id="batch-3",
        request={"audio_s3_url": "s3://b/a.mp3", "idempotency_key": "lilmac-batch-v9"},
        redis=redis,
    )
    retry_call = _setup(monkeypatch, tmp_path, store)
    render_priority.mark_waiting(redis, key_prefix="blast_test", job_id="user-9")

    with pytest.raises(_RetryRaised, match="windows_node_pool_saturated"):
        tasks.dispatch_to_windows.run("batch-3")

    assert store.get("batch-3").stage == "render_wait_capacity"
    assert retry_call["countdown"] >= 15.0
