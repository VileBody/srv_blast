# services/orchestrator/job_store.py
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar

import redis

from core.llm_worker_types import normalize_llm_worker_type
from .schemas import JobState, JobStatus


log = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def _env(key: str, default: str = "") -> str:
    import os
    return (os.environ.get(key, default) or "").strip()


def _redis_client_from_env() -> redis.Redis:
    host = _env("REDIS_HOST", "localhost")
    port = int(_env("REDIS_PORT", "6379") or "6379")
    username = _env("REDIS_USERNAME", "")
    password = _env("REDIS_PASSWORD", "")

    # decode_responses=True -> str, а не bytes
    return redis.Redis(
        host=host,
        port=port,
        username=username or None,
        password=password or None,
        decode_responses=True,
    )


T = TypeVar("T")

_RELEASE_SLOT_LUA = """
local key = KEYS[1]
local current = tonumber(redis.call('GET', key) or '0')
if current <= 0 then
  redis.call('SET', key, '0')
  return 0
end
return redis.call('DECR', key)
"""

_LUA_SET_STATUS = """
local key = KEYS[1]
local status = ARGV[1]
local stage_arg = ARGV[2]
local error_arg = ARGV[3]
local result_json = ARGV[4]
local now = tonumber(ARGV[5]) or 0
local ttl = tonumber(ARGV[6]) or 0

local raw = redis.call('GET', key)
if not raw then
  return nil
end

local obj = cjson.decode(raw)
local prev_status = tostring(obj.status or '')

obj.status = status
obj.updated_at = now
obj.version = tonumber(obj.version or 0) + 1

if stage_arg ~= '' then
  obj.stage = stage_arg
end

if status == 'QUEUED' and not obj.queued_at then
  obj.queued_at = now
end
if status == 'RUNNING' and not obj.started_at then
  obj.started_at = now
end
if (status == 'SUCCEEDED' or status == 'FAILED') and not obj.finished_at then
  obj.finished_at = now
end

if error_arg == '__CLEAR__' then
  obj.error = cjson.null
elseif error_arg ~= '__NONE__' then
  obj.error = error_arg
end

local patch_obj = cjson.decode(result_json or '{}')
if type(patch_obj) == 'table' then
  local has_patch = false
  for _, _ in pairs(patch_obj) do
    has_patch = true
    break
  end
  if has_patch then
    local merged = obj.result
    if type(merged) ~= 'table' then
      merged = {}
    end
    for k, v in pairs(patch_obj) do
      merged[k] = v
    end
    obj.result = merged
  end
end

local encoded = cjson.encode(obj)
if ttl > 0 then
  redis.call('SET', key, encoded, 'EX', ttl)
else
  redis.call('SET', key, encoded)
end
return {prev_status, encoded}
"""


@dataclass(frozen=True)
class JobStore:
    """
    Minimal Redis-backed job state store.

    Keys:
      - job:{job_id} -> JSON JobState (single object)
      - idem:{key}   -> job_id (string), for idempotency
    """
    r: redis.Redis
    key_prefix: str = "blast"

    @classmethod
    def from_env(cls) -> "JobStore":
        prefix = _env("JOBSTORE_PREFIX", "blast")
        return cls(r=_redis_client_from_env(), key_prefix=prefix)

    def _k_job(self, job_id: str) -> str:
        return f"{self.key_prefix}:job:{job_id}"

    def _k_idem(self, idem_key: str) -> str:
        return f"{self.key_prefix}:idem:{idem_key}"

    def _k_llm_inflight(self, worker_type: str) -> str:
        wt = normalize_llm_worker_type(worker_type)
        return f"{self.key_prefix}:llm_workers:inflight:{wt}:v1"

    def _redis_max_attempts(self) -> int:
        try:
            return max(1, int(_env("JOBSTORE_REDIS_MAX_ATTEMPTS", "5") or "5"))
        except Exception:
            return 5

    def _redis_backoff_s(self) -> float:
        try:
            return max(0.0, float(_env("JOBSTORE_REDIS_BACKOFF_S", "0.5") or "0.5"))
        except Exception:
            return 0.5

    def _job_ttl_seconds(self) -> int:
        try:
            return max(0, int(_env("JOBSTORE_JOB_TTL_SECONDS", "1209600") or "1209600"))
        except Exception:
            return 1209600

    def _idempotency_ttl_seconds(self) -> int:
        try:
            return max(0, int(_env("JOBSTORE_IDEMPOTENCY_TTL_SECONDS", "1209600") or "1209600"))
        except Exception:
            return 1209600

    def _finished_job_ttl_s(self) -> int:
        try:
            raw = _env("JOBSTORE_FINISHED_JOB_TTL_SECONDS", "")
            if not raw:
                return self._job_ttl_seconds()
            return max(0, int(raw))
        except Exception:
            return self._job_ttl_seconds()

    def _redis_call(self, op: str, fn: Callable[[], T]) -> T:
        """
        Deterministic retry for transient Redis disconnects/timeouts.
        We do NOT swallow errors: after N attempts we raise with context.
        """
        max_attempts = self._redis_max_attempts()
        backoff_s = self._redis_backoff_s()

        last: BaseException | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return fn()
            except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, OSError) as e:
                last = e
                if attempt >= max_attempts:
                    break
                sleep_s = min(5.0, backoff_s * (2 ** (attempt - 1)))
                log.warning("redis transient error op=%s attempt=%d/%d sleep=%.2fs err=%r", op, attempt, max_attempts, sleep_s, e)
                try:
                    # Force reconnect on next call.
                    self.r.connection_pool.disconnect()
                except Exception:
                    pass
                time.sleep(sleep_s)

        raise RuntimeError(f"redis_op_failed op={op} attempts={max_attempts} err={last!r}") from last

    # -------------------------
    # Core ops
    # -------------------------

    def get(self, job_id: str) -> Optional[JobState]:
        raw = self._redis_call("get", lambda: self.r.get(self._k_job(job_id)))
        if not raw:
            return None
        try:
            d = json.loads(raw)
            return JobState.model_validate(d)
        except Exception:
            return None

    def list_jobs(self, *, limit: Optional[int] = None) -> list[JobState]:
        pattern = f"{self.key_prefix}:job:*"
        keys = self._redis_call(
            "list_jobs_scan",
            lambda: list(self.r.scan_iter(match=pattern, count=500)),
        )
        if not keys:
            return []

        out: list[JobState] = []
        max_items = int(limit) if limit is not None else 0
        for i in range(0, len(keys), 200):
            batch = keys[i : i + 200]
            raw_values = self._redis_call(
                "list_jobs_mget",
                lambda b=batch: self.r.mget(b),
            ) or []
            for raw in raw_values:
                if not raw:
                    continue
                try:
                    out.append(JobState.model_validate(json.loads(raw)))
                except Exception:
                    continue

        out.sort(key=lambda st: float(st.created_at), reverse=True)
        if max_items > 0:
            return out[:max_items]
        return out

    def _put(self, st: JobState) -> JobState:
        key = self._k_job(st.job_id)
        payload = st.model_dump_json()
        ttl_s = self._job_ttl_seconds()
        if ttl_s > 0:
            self._redis_call("setex", lambda: self.r.set(key, payload, ex=ttl_s))
        else:
            self._redis_call("set", lambda: self.r.set(key, payload))
        return st

    def _is_retryable_idempotent_failure(self, st: JobState) -> bool:
        if st.status != "FAILED":
            return False
        err = str(st.error or "").lower()
        if not err:
            return False
        retryable_markers = (
            "capacity_exhausted",
            "llm_worker_disabled",
            "llm_workers_no_enabled_types",
            "queue_failed",
        )
        return any(marker in err for marker in retryable_markers)

    def new_job(
        self,
        *,
        request: Dict[str, Any],
        idempotency_key: Optional[str],
    ) -> Tuple[JobState, bool]:
        """
        Returns: (state, created)
          - created=False when idempotency hit.
        """
        idem_k = self._k_idem(idempotency_key) if idempotency_key else ""
        idem_ttl_s = self._idempotency_ttl_seconds()

        for _ in range(8):
            if idempotency_key:
                existing_job_id = self._redis_call("get_idem", lambda: self.r.get(idem_k))
                if existing_job_id:
                    st_existing = self.get(existing_job_id)
                    if st_existing and not self._is_retryable_idempotent_failure(st_existing):
                        return st_existing, False
                    # stale mapping or retryable failed state -> allow fresh attempt
                    self._redis_call("delete_idem", lambda: self.r.delete(idem_k))

            job_id = uuid.uuid4().hex
            now = _now()
            st = JobState(
                job_id=job_id,
                status="NEW",
                version=1,
                created_at=now,
                updated_at=now,
                queued_at=None,
                started_at=None,
                finished_at=None,
                stage=None,
                idempotency_key=idempotency_key,
                request=request or {},
                result=None,
                error=None,
            )

            if idempotency_key:
                # Claim idempotency atomically before creating job object to avoid duplicate creators.
                if idem_ttl_s > 0:
                    claimed = self._redis_call(
                        "setnx_idem_ex",
                        lambda: self.r.set(idem_k, job_id, nx=True, ex=idem_ttl_s),
                    )
                else:
                    claimed = self._redis_call(
                        "setnx_idem",
                        lambda: self.r.set(idem_k, job_id, nx=True),
                    )
                if not claimed:
                    continue

            try:
                self._put(st)
            except Exception:
                if idempotency_key:
                    # Best-effort cleanup of a claim that points to a missing job record.
                    try:
                        cur = self._redis_call("get_idem_after_put_error", lambda: self.r.get(idem_k))
                        if cur == job_id:
                            self._redis_call("delete_idem_after_put_error", lambda: self.r.delete(idem_k))
                    except Exception:
                        pass
                raise

            return st, True

        raise RuntimeError("new_job_failed_after_retries")

    def _release_llm_slot_for_state(self, st: JobState) -> None:
        req = st.request or {}
        raw = str(req.get("llm_worker_type") or "").strip()
        if not raw:
            return
        try:
            key = self._k_llm_inflight(raw)
        except Exception:
            return
        try:
            self._redis_call(
                "llm_workers_release_slot",
                lambda: self.r.eval(_RELEASE_SLOT_LUA, 1, key),
            )
        except Exception:
            # Keep status update deterministic even if slot release failed.
            pass

    def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        stage: Optional[str] = None,
        error: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> Optional[JobState]:
        """
        Atomic status update via Lua script (no read-modify-write race).
        """
        job_k = self._k_job(job_id)
        now_s = str(_now())
        job_ttl = self._finished_job_ttl_s() if status in {"SUCCEEDED", "FAILED"} else self._job_ttl_seconds()

        error_arg = "__NONE__"
        if error is not None:
            error_arg = error
        elif status == "SUCCEEDED":
            error_arg = "__CLEAR__"

        result_json = json.dumps(result, ensure_ascii=False) if result else "{}"

        raw = self._redis_call(
            "set_status_atomic",
            lambda: self.r.eval(
                _LUA_SET_STATUS,
                1,
                job_k,
                status,
                stage or "",
                error_arg,
                result_json,
                now_s,
                str(job_ttl),
            ),
        )
        if raw is None:
            return None

        prev_status = ""
        encoded = ""
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            prev_status = str(raw[0] or "")
            encoded = str(raw[1] or "")
        else:
            encoded = str(raw or "")

        if not encoded:
            return None

        try:
            stored = JobState.model_validate(json.loads(encoded))
        except Exception:
            # Safety net for malformed Redis payloads.
            return self.get(job_id)

        active_statuses = {"QUEUED", "RUNNING"}
        if prev_status in active_statuses and stored.status not in active_statuses:
            self._release_llm_slot_for_state(stored)
        return stored

    def patch_request(self, job_id: str, patch: Dict[str, Any]) -> Optional[JobState]:
        st = self.get(job_id)
        if not st:
            return None

        req = dict(st.request or {})
        req.update(patch or {})

        st2 = JobState(
            job_id=st.job_id,
            status=st.status,
            version=max(0, int(getattr(st, "version", 0))) + 1,
            created_at=st.created_at,
            updated_at=_now(),
            queued_at=st.queued_at,
            started_at=st.started_at,
            finished_at=st.finished_at,
            stage=st.stage,
            idempotency_key=st.idempotency_key,
            request=req,
            result=st.result,
            error=st.error,
        )
        return self._put(st2)
