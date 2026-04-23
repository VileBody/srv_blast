from __future__ import annotations

import sys
import types

if "redis" not in sys.modules:
    redis_stub = types.ModuleType("redis")

    class _DummyRedis:  # pragma: no cover - import stub only
        pass

    class _RedisExceptions:  # pragma: no cover - import stub only
        class ConnectionError(Exception):
            pass

        class TimeoutError(Exception):
            pass

    redis_stub.Redis = _DummyRedis
    redis_stub.exceptions = _RedisExceptions
    sys.modules["redis"] = redis_stub

from services.orchestrator.llm_workers import (
    ensure_enqueue_worker_available,
    LLMWorkerControl,
    LLMWorkersConfigPayload,
    choose_worker_type,
    release_worker_slot,
    reserve_worker_type_for_job,
    select_worker_type,
    set_config,
)


class _FakeRedis:
    def __init__(self) -> None:
        self._kv: dict[str, object] = {}

    def get(self, key: str):
        return self._kv.get(key)

    def set(self, key: str, value, nx: bool = False, ex: int | None = None):
        if nx and key in self._kv:
            return False
        self._kv[key] = value
        return True

    def mget(self, keys):
        return [self._kv.get(k) for k in keys]

    def incr(self, key: str) -> int:
        cur = int(self._kv.get(key, 0) or 0) + 1
        self._kv[key] = cur
        return cur

    def eval(self, script: str, numkeys: int, *args):
        if "local reservation_key = KEYS[1]" in script:
            reservation_key = str(args[0])
            inflight_key = str(args[1])
            worker_type = str(args[2])
            max_inflight = int(args[3])
            existing = str(self._kv.get(reservation_key, "") or "")
            if existing:
                return existing
            cur = int(self._kv.get(inflight_key, 0) or 0)
            if cur >= max_inflight:
                return ""
            self._kv[inflight_key] = cur + 1
            self._kv[reservation_key] = worker_type
            return worker_type
        if "redis.call('INCR'" in script:
            key = str(args[0])
            max_inflight = int(args[1])
            cur = int(self._kv.get(key, 0) or 0)
            if cur >= max_inflight:
                return 0
            self._kv[key] = cur + 1
            return 1
        if "redis.call('DECR'" in script:
            key = str(args[0])
            cur = int(self._kv.get(key, 0) or 0)
            if cur <= 0:
                self._kv[key] = 0
                return 0
            self._kv[key] = cur - 1
            return cur - 1
        raise AssertionError(f"Unexpected eval script: {script}")


class _FakeStore:
    def __init__(self) -> None:
        self.r = _FakeRedis()
        self.key_prefix = "test"

    def _redis_call(self, _op: str, fn):
        return fn()


def _workers(
    *,
    sdk: LLMWorkerControl,
    openrouter: LLMWorkerControl,
    hybrid: LLMWorkerControl,
    vertex_sdk_mix: LLMWorkerControl | None = None,
) -> dict[str, LLMWorkerControl]:
    return {
        "sdk": sdk,
        "openrouter": openrouter,
        "hybrid": hybrid,
        "vertex_sdk_mix": vertex_sdk_mix or LLMWorkerControl(enabled=False, weight=0, max_inflight=1),
    }


def test_choose_worker_type_weighted_round_robin_is_deterministic() -> None:
    store = _FakeStore()
    set_config(
        store,
        LLMWorkersConfigPayload(
            workers=_workers(
                sdk=LLMWorkerControl(enabled=True, weight=2, max_inflight=10),
                openrouter=LLMWorkerControl(enabled=True, weight=1, max_inflight=10),
                hybrid=LLMWorkerControl(enabled=True, weight=1, max_inflight=10),
            )
        ),
    )

    picked = [choose_worker_type(store).worker_type for _ in range(8)]
    assert picked == [
        "sdk",
        "sdk",
        "openrouter",
        "hybrid",
        "sdk",
        "sdk",
        "openrouter",
        "hybrid",
    ]


def test_select_worker_type_weighted_round_robin_does_not_consume_capacity() -> None:
    store = _FakeStore()
    set_config(
        store,
        LLMWorkersConfigPayload(
            workers=_workers(
                sdk=LLMWorkerControl(enabled=True, weight=2, max_inflight=1),
                openrouter=LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
                hybrid=LLMWorkerControl(enabled=False, weight=1, max_inflight=1),
            )
        ),
    )

    picked = [select_worker_type(store).worker_type for _ in range(3)]

    assert picked == ["sdk", "sdk", "openrouter"]
    assert int(store.r.get("test:llm_workers:inflight:sdk:v1") or 0) == 0
    assert int(store.r.get("test:llm_workers:inflight:openrouter:v1") or 0) == 0


def test_ensure_enqueue_worker_available_accepts_generic_queue_first_flow() -> None:
    store = _FakeStore()
    set_config(
        store,
        LLMWorkersConfigPayload(
            workers=_workers(
                sdk=LLMWorkerControl(enabled=True, weight=0, max_inflight=1),
                openrouter=LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
                hybrid=LLMWorkerControl(enabled=False, weight=1, max_inflight=1),
            )
        ),
    )

    assert ensure_enqueue_worker_available(store) == ""
    assert ensure_enqueue_worker_available(store, requested="sdk") == "sdk"


def test_choose_worker_type_skips_exhausted_type_by_max_inflight() -> None:
    store = _FakeStore()
    set_config(
        store,
        LLMWorkersConfigPayload(
            workers=_workers(
                sdk=LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
                openrouter=LLMWorkerControl(enabled=True, weight=1, max_inflight=2),
                hybrid=LLMWorkerControl(enabled=False, weight=1, max_inflight=1),
            )
        ),
    )
    store.r.set("test:llm_workers:inflight:sdk:v1", 1)

    selected = choose_worker_type(store)
    assert selected.worker_type == "openrouter"


def test_choose_worker_type_requested_disabled_or_exhausted_raises() -> None:
    store = _FakeStore()
    set_config(
        store,
        LLMWorkersConfigPayload(
            workers=_workers(
                sdk=LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
                openrouter=LLMWorkerControl(enabled=False, weight=1, max_inflight=1),
                hybrid=LLMWorkerControl(enabled=False, weight=1, max_inflight=1),
            )
        ),
    )
    store.r.set("test:llm_workers:inflight:sdk:v1", 1)

    try:
        choose_worker_type(store, requested="openrouter")
        assert False, "expected disabled worker error"
    except RuntimeError as e:
        assert "llm_worker_disabled" in str(e)

    try:
        choose_worker_type(store, requested="sdk")
        assert False, "expected exhausted worker error"
    except RuntimeError as e:
        assert "llm_worker_capacity_exhausted" in str(e)


def test_release_worker_slot_decrements_and_clamps_at_zero() -> None:
    store = _FakeStore()
    store.r.set("test:llm_workers:inflight:sdk:v1", 2)
    assert release_worker_slot(store, "sdk") == 1
    assert release_worker_slot(store, "sdk") == 0
    assert release_worker_slot(store, "sdk") == 0


def test_set_config_guardrail_rejects_all_disabled() -> None:
    store = _FakeStore()
    try:
        set_config(
            store,
            LLMWorkersConfigPayload(
                workers=_workers(
                    sdk=LLMWorkerControl(enabled=False, weight=0, max_inflight=1),
                    openrouter=LLMWorkerControl(enabled=False, weight=0, max_inflight=1),
                    hybrid=LLMWorkerControl(enabled=False, weight=0, max_inflight=1),
                )
            ),
        )
        assert False, "expected guardrail error"
    except RuntimeError as e:
        assert "guardrail" in str(e)


def test_burst_reservation_does_not_oversubscribe_backends() -> None:
    store = _FakeStore()
    set_config(
        store,
        LLMWorkersConfigPayload(
            workers=_workers(
                sdk=LLMWorkerControl(enabled=True, weight=1, max_inflight=2),
                openrouter=LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
                hybrid=LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
            )
        ),
    )

    successes = 0
    failures = 0
    for _ in range(20):
        try:
            choose_worker_type(store)
            successes += 1
        except RuntimeError as e:
            assert "capacity_exhausted" in str(e)
            failures += 1

    assert successes == 4
    assert failures == 16
    assert int(store.r.get("test:llm_workers:inflight:sdk:v1") or 0) <= 2
    assert int(store.r.get("test:llm_workers:inflight:openrouter:v1") or 0) <= 1
    assert int(store.r.get("test:llm_workers:inflight:hybrid:v1") or 0) <= 1


def test_reserve_worker_type_for_job_is_idempotent_for_same_job() -> None:
    store = _FakeStore()
    set_config(
        store,
        LLMWorkersConfigPayload(
            workers=_workers(
                sdk=LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
                openrouter=LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
                hybrid=LLMWorkerControl(enabled=False, weight=1, max_inflight=1),
            )
        ),
    )

    first = reserve_worker_type_for_job(store, job_id="job-1", requested="sdk")
    second = reserve_worker_type_for_job(store, job_id="job-1", requested="sdk")

    assert first.worker_type == "sdk"
    assert second.worker_type == "sdk"
    assert int(store.r.get("test:llm_workers:inflight:sdk:v1") or 0) == 1


def test_reserve_worker_type_for_job_respects_capacity_across_jobs() -> None:
    store = _FakeStore()
    set_config(
        store,
        LLMWorkersConfigPayload(
            workers=_workers(
                sdk=LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
                openrouter=LLMWorkerControl(enabled=False, weight=1, max_inflight=1),
                hybrid=LLMWorkerControl(enabled=False, weight=1, max_inflight=1),
            )
        ),
    )

    reserve_worker_type_for_job(store, job_id="job-1", requested="sdk")
    try:
        reserve_worker_type_for_job(store, job_id="job-2", requested="sdk")
        assert False, "expected capacity exhaustion for second job"
    except RuntimeError as e:
        assert "capacity_exhausted" in str(e)
