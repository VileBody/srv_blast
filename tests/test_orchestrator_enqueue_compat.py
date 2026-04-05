from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.orchestrator import app as orchestrator_app
from services.orchestrator.schemas import JobState


class _StoreWithoutPatchRequest:
    def __init__(self) -> None:
        self.items: dict[str, JobState] = {}
        self._seq = 0

    def new_job(self, *, request, idempotency_key):
        self._seq += 1
        job_id = f"job-{self._seq}"
        st = JobState(
            job_id=job_id,
            status="NEW",
            created_at=1.0,
            updated_at=1.0,
            queued_at=None,
            started_at=None,
            finished_at=None,
            stage=None,
            idempotency_key=idempotency_key,
            request=dict(request or {}),
            result=None,
            error=None,
        )
        self.items[job_id] = st
        return st, True

    def get(self, job_id: str):
        return self.items.get(job_id)

    def _put(self, st: JobState):
        self.items[st.job_id] = st
        return st

    def set_status(self, job_id: str, status: str, *, stage=None, error=None, result=None):
        st = self.items.get(job_id)
        if st is None:
            return None
        merged = dict(st.result or {})
        if result:
            merged.update(result)
        st2 = JobState(
            job_id=st.job_id,
            status=status,
            created_at=st.created_at,
            updated_at=st.updated_at + 1.0,
            queued_at=st.queued_at,
            started_at=st.started_at,
            finished_at=st.finished_at,
            stage=stage if stage is not None else st.stage,
            idempotency_key=st.idempotency_key,
            request=dict(st.request or {}),
            result=merged or None,
            error=error,
        )
        self.items[job_id] = st2
        return st2


def test_send_audio_s3_works_when_job_store_has_no_patch_request(monkeypatch) -> None:
    store = _StoreWithoutPatchRequest()
    enqueued: list[str] = []

    monkeypatch.setattr(orchestrator_app.JobStore, "from_env", classmethod(lambda cls: store))
    monkeypatch.setattr(
        orchestrator_app,
        "choose_worker_type",
        lambda _store, requested=None: SimpleNamespace(worker_type="sdk"),
    )
    monkeypatch.setattr(
        orchestrator_app,
        "build_job_sdk",
        SimpleNamespace(delay=lambda job_id: enqueued.append(str(job_id))),
    )
    monkeypatch.setattr(
        orchestrator_app,
        "ensure_descriptions_bundle",
        lambda **kwargs: SimpleNamespace(ok=True, action="skip", bundle_path="n/a", reason=""),
    )

    app = orchestrator_app.create_app()
    with TestClient(app) as client:
        resp = client.post(
            "/send_audio_s3",
            json={
                "audio_s3_url": "s3://bucket/raw_audio/example.mp3",
                "idempotency_key": "idem-1",
                "overlay_enabled": False,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "QUEUED"
    assert body["created"] is True

    job_id = body["job_id"]
    st = store.get(job_id)
    assert st is not None
    assert st.request.get("llm_worker_type") == "sdk"
    assert st.request.get("overlay_enabled") is False
    assert enqueued == [job_id]
