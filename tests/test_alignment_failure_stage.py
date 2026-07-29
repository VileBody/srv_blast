from __future__ import annotations

from types import SimpleNamespace

from mlcore.alignment.client import AlignmentServiceError
from services.orchestrator import celery_app as celery_module


def test_alignment_error_is_persisted_as_alignment_stage(monkeypatch) -> None:
    calls: list[dict] = []

    class _Store:
        def get(self, _job_id):
            return SimpleNamespace(status="RUNNING")

        def set_status(self, job_id, status, **kwargs):
            calls.append({"job_id": job_id, "status": status, **kwargs})

    monkeypatch.setattr(
        celery_module.JobStore,
        "from_env",
        classmethod(lambda _cls: _Store()),
    )
    task = celery_module.JobBoundTask()
    task.name = "orchestrator.build_job_sdk"
    error = AlignmentServiceError("ALIGNMENT_TIMEOUT", "timed out")

    task.on_failure(
        error,
        "task-id",
        ("job-id",),
        {},
        SimpleNamespace(traceback="trace"),
    )

    assert calls[-1]["status"] == "FAILED"
    assert calls[-1]["stage"] == "alignment"
