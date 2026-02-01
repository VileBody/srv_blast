# services/orchestrator/job_store.py
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import redis

from .schemas import JobState, JobStatus


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

    # -------------------------
    # Core ops
    # -------------------------

    def get(self, job_id: str) -> Optional[JobState]:
        raw = self.r.get(self._k_job(job_id))
        if not raw:
            return None
        try:
            d = json.loads(raw)
            return JobState.model_validate(d)
        except Exception:
            return None

    def _put(self, st: JobState) -> JobState:
        self.r.set(self._k_job(st.job_id), st.model_dump_json())
        return st

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
        if idempotency_key:
            idem_k = self._k_idem(idempotency_key)
            existing_job_id = self.r.get(idem_k)
            if existing_job_id:
                st = self.get(existing_job_id)
                if st:
                    return st, False
                # stale mapping -> delete and continue
                self.r.delete(idem_k)

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

        self._put(st)

        if idempotency_key:
            # idempotency mapping (no TTL by default)
            self.r.set(self._k_idem(idempotency_key), job_id)

        return st, True

    def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        stage: Optional[str] = None,
        error: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> Optional[JobState]:
        st = self.get(job_id)
        if not st:
            return None

        now = _now()

        queued_at = st.queued_at
        started_at = st.started_at
        finished_at = st.finished_at

        if status == "QUEUED" and queued_at is None:
            queued_at = now
        if status == "RUNNING" and started_at is None:
            started_at = now
        if status in ("SUCCEEDED", "FAILED") and finished_at is None:
            finished_at = now

        merged_result = st.result or None
        if result is not None:
            # shallow merge (result wins)
            base = dict(merged_result or {})
            base.update(result)
            merged_result = base

        # if status becomes SUCCEEDED, clear error
        final_error = error if status != "SUCCEEDED" else None
        if final_error is None:
            final_error = st.error

        st2 = JobState(
            job_id=st.job_id,
            status=status,
            created_at=st.created_at,
            updated_at=now,
            queued_at=queued_at,
            started_at=started_at,
            finished_at=finished_at,
            stage=stage if stage is not None else st.stage,
            idempotency_key=st.idempotency_key,
            request=st.request or {},
            result=merged_result,
            error=final_error,
        )

        return self._put(st2)
