from __future__ import annotations

import json
import os
from typing import Dict, Optional

from pydantic import BaseModel, Field

from core.llm_worker_types import (
    LLM_WORKER_TYPES,
    LLM_WORKER_TYPE_HYBRID,
    LLM_WORKER_TYPE_OPENROUTER,
    LLM_WORKER_TYPE_SDK,
    normalize_llm_worker_type,
)
from .job_store import JobStore


def _bool_env(name: str, default: bool) -> bool:
    raw = (os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _int_env(name: str, default: int, *, min_value: int = 0) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        v = int(raw)
    except Exception:
        return int(default)
    return max(min_value, int(v))


class LLMWorkerControl(BaseModel):
    enabled: bool = True
    weight: int = Field(default=1, ge=0, le=1000)
    max_inflight: int = Field(default=4, ge=1, le=1000)


class LLMWorkersConfigPayload(BaseModel):
    workers: Dict[str, LLMWorkerControl]


class LLMWorkerRuntimeStatus(BaseModel):
    enabled: bool
    weight: int
    max_inflight: int
    inflight: int
    available_slots: int


class LLMWorkersStatusPayload(BaseModel):
    workers: Dict[str, LLMWorkerRuntimeStatus]


class LLMWorkerSelection(BaseModel):
    worker_type: str
    workers: Dict[str, LLMWorkerRuntimeStatus]


def _config_key(prefix: str) -> str:
    return f"{prefix}:llm_workers:config:v1"


def _rr_cursor_key(prefix: str) -> str:
    return f"{prefix}:llm_workers:rr_cursor:v1"


def _inflight_key(prefix: str, worker_type: str) -> str:
    wt = normalize_llm_worker_type(worker_type)
    return f"{prefix}:llm_workers:inflight:{wt}:v1"


def _default_config() -> Dict[str, LLMWorkerControl]:
    return {
        LLM_WORKER_TYPE_SDK: LLMWorkerControl(
            enabled=_bool_env("LLM_WORKER_SDK_ENABLED", True),
            weight=_int_env("LLM_WORKER_SDK_WEIGHT", 1, min_value=0),
            max_inflight=_int_env("LLM_WORKER_SDK_MAX_INFLIGHT", 4, min_value=1),
        ),
        LLM_WORKER_TYPE_OPENROUTER: LLMWorkerControl(
            enabled=_bool_env("LLM_WORKER_OPENROUTER_ENABLED", False),
            weight=_int_env("LLM_WORKER_OPENROUTER_WEIGHT", 1, min_value=0),
            max_inflight=_int_env("LLM_WORKER_OPENROUTER_MAX_INFLIGHT", 4, min_value=1),
        ),
        LLM_WORKER_TYPE_HYBRID: LLMWorkerControl(
            enabled=_bool_env("LLM_WORKER_HYBRID_ENABLED", False),
            weight=_int_env("LLM_WORKER_HYBRID_WEIGHT", 1, min_value=0),
            max_inflight=_int_env("LLM_WORKER_HYBRID_MAX_INFLIGHT", 4, min_value=1),
        ),
    }


def _normalize_config(raw: Dict[str, object] | None) -> Dict[str, LLMWorkerControl]:
    defaults = _default_config()
    if not isinstance(raw, dict):
        return defaults

    out = dict(defaults)
    for worker_type in LLM_WORKER_TYPES:
        value = raw.get(worker_type)
        if not isinstance(value, dict):
            continue
        try:
            out[worker_type] = LLMWorkerControl.model_validate(value)
        except Exception:
            continue
    return out


def _has_admission_capacity(cfg: Dict[str, LLMWorkerControl]) -> bool:
    for wt in LLM_WORKER_TYPES:
        c = cfg[wt]
        if c.enabled and c.weight > 0 and c.max_inflight > 0:
            return True
    return False


def ensure_config_initialized(store: JobStore) -> Dict[str, LLMWorkerControl]:
    defaults = _default_config()
    body = {
        worker_type: defaults[worker_type].model_dump(mode="json")
        for worker_type in LLM_WORKER_TYPES
    }
    store._redis_call(
        "llm_workers_init_config",
        lambda: store.r.setnx(_config_key(store.key_prefix), json.dumps(body, ensure_ascii=False)),
    )
    return get_config(store)


def get_config(store: JobStore) -> Dict[str, LLMWorkerControl]:
    raw = store._redis_call("llm_workers_get_config", lambda: store.r.get(_config_key(store.key_prefix)))
    if not raw:
        return _default_config()
    try:
        obj = json.loads(raw)
    except Exception:
        return _default_config()
    return _normalize_config(obj if isinstance(obj, dict) else None)


def set_config(store: JobStore, payload: LLMWorkersConfigPayload) -> Dict[str, LLMWorkerControl]:
    cfg: Dict[str, LLMWorkerControl] = {}
    for worker_type in LLM_WORKER_TYPES:
        if worker_type not in payload.workers:
            raise RuntimeError(f"missing worker config for {worker_type}")
        cfg[worker_type] = payload.workers[worker_type]

    if not _has_admission_capacity(cfg):
        raise RuntimeError(
            "llm_workers_guardrail: keep at least one enabled worker with weight > 0 and max_inflight > 0"
        )

    body = {
        worker_type: cfg[worker_type].model_dump(mode="json")
        for worker_type in LLM_WORKER_TYPES
    }
    store._redis_call(
        "llm_workers_set_config",
        lambda: store.r.set(_config_key(store.key_prefix), json.dumps(body, ensure_ascii=False)),
    )
    return cfg


def get_inflight_counts(store: JobStore) -> Dict[str, int]:
    keys = [_inflight_key(store.key_prefix, wt) for wt in LLM_WORKER_TYPES]
    raw_values = store._redis_call("llm_workers_get_inflight", lambda: store.r.mget(keys))
    out: Dict[str, int] = {}
    for idx, wt in enumerate(LLM_WORKER_TYPES):
        raw = raw_values[idx] if idx < len(raw_values) else None
        try:
            out[wt] = max(0, int(raw or 0))
        except Exception:
            out[wt] = 0
    return out


def get_runtime_status(store: JobStore) -> Dict[str, LLMWorkerRuntimeStatus]:
    cfg = get_config(store)
    inflight = get_inflight_counts(store)
    out: Dict[str, LLMWorkerRuntimeStatus] = {}
    for worker_type in LLM_WORKER_TYPES:
        ctrl = cfg[worker_type]
        count = int(inflight.get(worker_type, 0))
        out[worker_type] = LLMWorkerRuntimeStatus(
            enabled=bool(ctrl.enabled),
            weight=int(ctrl.weight),
            max_inflight=int(ctrl.max_inflight),
            inflight=count,
            available_slots=max(0, int(ctrl.max_inflight) - count),
        )
    return out


_RESERVE_SLOT_LUA = """
local key = KEYS[1]
local max_inflight = tonumber(ARGV[1]) or 0
local current = tonumber(redis.call('GET', key) or '0')
if current >= max_inflight then
  return 0
end
redis.call('INCR', key)
return 1
"""

_RELEASE_SLOT_LUA = """
local key = KEYS[1]
local current = tonumber(redis.call('GET', key) or '0')
if current <= 0 then
  redis.call('SET', key, '0')
  return 0
end
return redis.call('DECR', key)
"""


def _try_reserve_slot(store: JobStore, *, worker_type: str, max_inflight: int) -> bool:
    key = _inflight_key(store.key_prefix, worker_type)
    rv = store._redis_call(
        "llm_workers_reserve_slot",
        lambda: store.r.eval(_RESERVE_SLOT_LUA, 1, key, int(max_inflight)),
    )
    try:
        return int(rv) == 1
    except Exception:
        return False


def release_worker_slot(store: JobStore, worker_type: str) -> int:
    wt = normalize_llm_worker_type(worker_type)
    key = _inflight_key(store.key_prefix, wt)
    rv = store._redis_call(
        "llm_workers_release_slot",
        lambda: store.r.eval(_RELEASE_SLOT_LUA, 1, key),
    )
    try:
        return max(0, int(rv))
    except Exception:
        return 0


def reserve_worker_type(store: JobStore, *, requested: Optional[str] = None) -> LLMWorkerSelection:
    cfg = get_config(store)

    if requested:
        worker_type = normalize_llm_worker_type(requested)
        row = cfg[worker_type]
        if not row.enabled:
            raise RuntimeError(f"llm_worker_disabled: {worker_type}")
        if not _try_reserve_slot(store, worker_type=worker_type, max_inflight=int(row.max_inflight)):
            raise RuntimeError(f"llm_worker_capacity_exhausted: {worker_type}")
        return LLMWorkerSelection(worker_type=worker_type, workers=get_runtime_status(store))

    weighted: list[str] = []
    for worker_type in LLM_WORKER_TYPES:
        row = cfg[worker_type]
        if not row.enabled:
            continue
        if row.weight <= 0:
            continue
        weighted.extend([worker_type] * int(row.weight))
    if not weighted:
        raise RuntimeError("llm_workers_no_enabled_types")

    seq = int(
        store._redis_call(
            "llm_workers_rr_incr",
            lambda: store.r.incr(_rr_cursor_key(store.key_prefix)),
        )
    ) - 1
    start = seq % len(weighted)
    for i in range(len(weighted)):
        candidate = weighted[(start + i) % len(weighted)]
        row = cfg[candidate]
        if _try_reserve_slot(store, worker_type=candidate, max_inflight=int(row.max_inflight)):
            return LLMWorkerSelection(worker_type=candidate, workers=get_runtime_status(store))

    raise RuntimeError("llm_workers_capacity_exhausted")


# Backward-compatible alias used by early integration code/tests.
def choose_worker_type(store: JobStore, *, requested: Optional[str] = None) -> LLMWorkerSelection:
    return reserve_worker_type(store, requested=requested)
