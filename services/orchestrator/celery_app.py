# services/orchestrator/celery_app.py
from __future__ import annotations

import time
from typing import Any, Optional

from celery import Celery, Task

from .config import SETTINGS
from .job_store import JobStore


def _now() -> float:
    return time.time()


class JobBoundTask(Task):
    """
    Base Task that keeps JobStore in sync with Celery outcomes.

    Declarative contract:
      - First arg MUST be job_id: str  (for our tasks)
      - Tasks can raise; we sync JobStore here (on_failure / on_retry).
    """
    abstract = True

    def _job_id_from_args(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[str]:
        if args and isinstance(args[0], str) and args[0].strip():
            return args[0].strip()
        jid = kwargs.get("job_id")
        if isinstance(jid, str) and jid.strip():
            return jid.strip()
        return None

    def _stage_name(self) -> str:
        name = (self.name or "").lower()
        if "build_job" in name:
            return "build"
        if "dispatch_to_windows" in name:
            return "dispatch"
        if "poll_windows_render" in name:
            return "poll"
        return "task"

    def _set_failed(self, job_id: str, *, error: str) -> None:
        try:
            store = JobStore.from_env()
            st = store.get(job_id)
            # Once SUCCEEDED, do not let subsequent task failures overwrite it.
            if st and st.status == "SUCCEEDED":
                return
            store.set_status(job_id, "FAILED", stage=self._stage_name(), error=error)
        except Exception:
            pass

    def _set_retrying(self, job_id: str, *, error: str) -> None:
        try:
            store = JobStore.from_env()
            st = store.get(job_id)
            # Once SUCCEEDED, do not let retries move it back to RUNNING.
            if st and st.status == "SUCCEEDED":
                return
            store.set_status(job_id, "RUNNING", stage=f"{self._stage_name()}_retry", error=error)
        except Exception:
            pass

    def on_retry(  # type: ignore[override]
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        job_id = self._job_id_from_args(args, kwargs)
        if not job_id:
            return
        retries = getattr(self.request, "retries", 0) if getattr(self, "request", None) else 0
        eta = getattr(self.request, "eta", None) if getattr(self, "request", None) else None
        err = f"celery_retry stage={self._stage_name()} retries={retries} eta={eta} exc={exc!r}"
        self._set_retrying(job_id, error=err)

    def on_failure(  # type: ignore[override]
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        job_id = self._job_id_from_args(args, kwargs)
        if not job_id:
            return

        tb = ""
        try:
            tb = str(getattr(einfo, "traceback", "") or "")
        except Exception:
            tb = ""
        tb_tail = tb[-9000:] if tb else ""
        err = f"celery_failed stage={self._stage_name()} exc={exc!r}\n--- traceback (tail) ---\n{tb_tail}\n"
        self._set_failed(job_id, error=err)


celery_app = Celery(
    "orchestrator",
    broker=SETTINGS.celery_broker_url or None,
    backend=SETTINGS.celery_result_backend or None,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # We persist job state in our own Redis JobStore. Celery's own result backend
    # (especially STARTED state) is an extra Redis dependency and can fail the task
    # *before* executing its body if Redis drops the connection.
    # Keep behavior deterministic: do not rely on Celery result backend at all.
    task_track_started=False,
    task_ignore_result=True,
    task_store_errors_even_if_ignored=False,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    broker_connection_retry_on_startup=True,

    # Queues
    task_default_queue=SETTINGS.celery_queue_build,
    task_routes={
        "orchestrator.build_job": {"queue": SETTINGS.celery_queue_build},
        "orchestrator.build_job_sdk": {"queue": SETTINGS.celery_queue_build},
        "orchestrator.build_job_openrouter": {"queue": SETTINGS.celery_queue_build},
        "orchestrator.build_job_hybrid": {"queue": SETTINGS.celery_queue_build},
        "orchestrator.build_job_vertex_sdk_mix": {"queue": SETTINGS.celery_queue_build},
        "orchestrator.dispatch_to_windows": {"queue": SETTINGS.celery_queue_render},
        "orchestrator.poll_windows_render": {"queue": SETTINGS.celery_queue_render},
    },
)

# Use our base task class
celery_app.Task = JobBoundTask

# ✅ DECLARATIVE REGISTRATION: import tasks explicitly (NO autodiscover)
# This MUST be after celery_app is created.
from . import tasks as _tasks  # noqa: F401,E402
