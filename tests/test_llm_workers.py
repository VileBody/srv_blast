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
    LLMWorkerControl,
    LLMWorkersConfigPayload,
    choose_worker_type,
    release_worker_slot,
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


def test_choose_worker_type_weighted_round_robin_is_deterministic() -> None:
    store = _FakeStore()
    set_config(
        store,
        LLMWorkersConfigPayload(
            workers={
                "sdk": LLMWorkerControl(enabled=True, weight=2, max_inflight=10),
                "openrouter": LLMWorkerControl(enabled=True, weight=1, max_inflight=10),
                "hybrid": LLMWorkerControl(enabled=True, weight=1, max_inflight=10),
            }
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


def test_choose_worker_type_skips_exhausted_type_by_max_inflight() -> None:
    store = _FakeStore()
    set_config(
        store,
        LLMWorkersConfigPayload(
            workers={
                "sdk": LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
                "openrouter": LLMWorkerControl(enabled=True, weight=1, max_inflight=2),
                "hybrid": LLMWorkerControl(enabled=False, weight=1, max_inflight=1),
            }
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
            workers={
                "sdk": LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
                "openrouter": LLMWorkerControl(enabled=False, weight=1, max_inflight=1),
                "hybrid": LLMWorkerControl(enabled=False, weight=1, max_inflight=1),
            }
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
                workers={
                    "sdk": LLMWorkerControl(enabled=False, weight=0, max_inflight=1),
                    "openrouter": LLMWorkerControl(enabled=False, weight=0, max_inflight=1),
                    "hybrid": LLMWorkerControl(enabled=False, weight=0, max_inflight=1),
                }
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
            workers={
                "sdk": LLMWorkerControl(enabled=True, weight=1, max_inflight=2),
                "openrouter": LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
                "hybrid": LLMWorkerControl(enabled=True, weight=1, max_inflight=1),
            }
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
