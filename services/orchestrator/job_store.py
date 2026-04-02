# services/orchestrator/job_store.py
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

import redis

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

# ---------------------------------------------------------------------------
# Lua scripts for atomic operations
# ---------------------------------------------------------------------------

# Atomic new_job: SET NX idem key -> if already exists, return existing job.
# If FAILED job found for existing idem key, allow re-creation.
# KEYS[1] = idem key (or "" if no idempotency)
# KEYS[2] = job key
# ARGV[1] = job_id
# ARGV[2] = job JSON
# ARGV[3] = idem_ttl_s (0 = no TTL)
# ARGV[4] = "1" if idempotency_key is provided, else "0"
# Returns: [created (0/1), job_json]
_LUA_NEW_JOB = """
local has_idem = ARGV[4] == "1"
if has_idem then
    local idem_key = KEYS[1]
    local existing_job_id = redis.call("GET", idem_key)
    if existing_job_id then
        local existing_key = KEYS[2]:gsub("[^:]+$", "") .. existing_job_id
        local existing_raw = redis.call("GET", existing_key)
        if existing_raw then
            local ok, obj = pcall(cjson.decode, existing_raw)
            if ok and type(obj) == "table" then
                local st = obj["status"] or ""
                if st ~= "FAILED" then
                    return {0, existing_raw}
                end
                -- FAILED job: delete old idem mapping and old job, allow re-creation
                redis.call("DEL", existing_key)
            end
        end
        redis.call("DEL", idem_key)
    end
end

-- Create new job
local job_key = KEYS[2]
redis.call("SET", job_key, ARGV[2])

if has_idem then
    local idem_key = KEYS[1]
    local ttl = tonumber(ARGV[3]) or 0
    if ttl > 0 then
        redis.call("SET", idem_key, ARGV[1], "EX", ttl)
    else
        redis.call("SET", idem_key, ARGV[1])
    end
end

return {1, ARGV[2]}
"""

# Atomic set_status: read-modify-write in one round-trip.
# KEYS[1] = job key
# ARGV[1] = new status
# ARGV[2] = stage (or "" to keep existing)
# ARGV[3] = error (or "__NONE__" to keep existing, "__CLEAR__" to clear)
# ARGV[4] = result JSON to shallow-merge (or "{}" for none)
# ARGV[5] = now timestamp
# ARGV[6] = job_ttl_s for finished jobs (0 = no TTL)
# Returns: updated job JSON, or nil if job not found
_LUA_SET_STATUS = """
local raw = redis.call("GET", KEYS[1])
if not raw then
    return nil
end

local ok, st = pcall(cjson.decode, raw)
if not ok or type(st) ~= "table" then
    return nil
end

local new_status = ARGV[1]
local new_stage = ARGV[2]
local new_error = ARGV[3]
local merge_result_json = ARGV[4]
local now = tonumber(ARGV[5])
local job_ttl = tonumber(ARGV[6]) or 0

st["status"] = new_status
st["updated_at"] = now

if new_stage ~= "" then
    st["stage"] = new_stage
end

-- Timestamps: set only on first transition
if new_status == "QUEUED" and (st["queued_at"] == nil or st["queued_at"] == false) then
    st["queued_at"] = now
end
if new_status == "RUNNING" and (st["started_at"] == nil or st["started_at"] == false) then
    st["started_at"] = now
end
if (new_status == "SUCCEEDED" or new_status == "FAILED") and (st["finished_at"] == nil or st["finished_at"] == false) then
    st["finished_at"] = now
end

-- Result merge
if merge_result_json ~= "{}" then
    local rok, new_result = pcall(cjson.decode, merge_result_json)
    if rok and type(new_result) == "table" then
        local base = st["result"]
        if type(base) ~= "table" then
            base = {}
        end
        for k, v in pairs(new_result) do
            base[k] = v
        end
        st["result"] = base
    end
end

-- Error handling
if new_status == "SUCCEEDED" then
    st["error"] = nil
elseif new_error == "__CLEAR__" then
    st["error"] = nil
elseif new_error ~= "__NONE__" then
    st["error"] = new_error
end
-- if "__NONE__", keep existing error

local updated = cjson.encode(st)
redis.call("SET", KEYS[1], updated)

-- Apply TTL on terminal states
if (new_status == "SUCCEEDED" or new_status == "FAILED") and job_ttl > 0 then
    redis.call("EXPIRE", KEYS[1], job_ttl)
end

return updated
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

    def _idem_ttl_s(self) -> int:
        try:
            return max(0, int(_env("JOBSTORE_IDEM_TTL_S", "604800") or "604800"))  # 7 days default
        except Exception:
            return 604800

    def _finished_job_ttl_s(self) -> int:
        try:
            return max(0, int(_env("JOBSTORE_FINISHED_JOB_TTL_S", "0") or "0"))  # 0 = no TTL
        except Exception:
            return 0

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

    def _put(self, st: JobState) -> JobState:
        self._redis_call("set", lambda: self.r.set(self._k_job(st.job_id), st.model_dump_json()))
        return st

    def new_job(
        self,
        *,
        request: Dict[str, Any],
        idempotency_key: Optional[str],
    ) -> Tuple[JobState, bool]:
        """
        Atomic job creation with idempotency.

        Returns: (state, created)
          - created=False when idempotency hit (non-FAILED existing job).
          - If existing job is FAILED, it is deleted and a new one is created
            (retry-after-failure semantics).
        """
        job_id = uuid.uuid4().hex
        now = _now()

        st = JobState(
            job_id=job_id,
            status="NEW",
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

        has_idem = bool(idempotency_key)
        idem_k = self._k_idem(idempotency_key) if has_idem else ""
        job_k = self._k_job(job_id)
        idem_ttl = self._idem_ttl_s()

        result = self._redis_call(
            "new_job_atomic",
            lambda: self.r.eval(
                _LUA_NEW_JOB,
                2,
                idem_k or "__unused__",
                job_k,
                job_id,
                st.model_dump_json(),
                str(idem_ttl),
                "1" if has_idem else "0",
            ),
        )

        created = int(result[0]) == 1
        job_json = result[1]
        try:
            d = json.loads(job_json)
            return JobState.model_validate(d), created
        except Exception:
            return st, created

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
        job_ttl = self._finished_job_ttl_s()

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
        try:
            d = json.loads(raw)
            return JobState.model_validate(d)
        except Exception:
            return None

    def list_jobs(self, *, status_filter: Optional[str] = None, limit: int = 200) -> List[JobState]:
        """
        Scan-based listing. Use sparingly (admin/debug only, NOT hot path).
        """
        pattern = f"{self.key_prefix}:job:*"
        out: List[JobState] = []
        for key in self.r.scan_iter(match=pattern, count=200):
            if len(out) >= limit:
                break
            raw = self.r.get(key)
            if not raw:
                continue
            try:
                d = json.loads(raw)
                st = JobState.model_validate(d)
            except Exception:
                continue
            if status_filter and st.status != status_filter:
                continue
            out.append(st)
        return out
