from __future__ import annotations

import sys
import time
import types
from dataclasses import replace
from types import SimpleNamespace

from fastapi.testclient import TestClient

if "redis" not in sys.modules:
    redis_stub = types.ModuleType("redis")

    class _DummyRedis:
        def __init__(self, *args, **kwargs):
            pass

    class _RedisExceptions:
        class ConnectionError(Exception):
            pass

        class TimeoutError(Exception):
            pass

    redis_stub.Redis = _DummyRedis
    redis_stub.exceptions = _RedisExceptions
    sys.modules["redis"] = redis_stub

if "celery" not in sys.modules:
    celery_stub = types.ModuleType("celery")

    class _DummyInspect:
        def active(self):
            return {}

        def reserved(self):
            return {}

        def scheduled(self):
            return {}

    class _DummyControl:
        def inspect(self, timeout=1.0):
            return _DummyInspect()

        def revoke(self, task_id, terminate=False, signal=None):
            return None

    class _DummyTask:
        request = SimpleNamespace(retries=0, eta=None)

    class _DummyCelery:
        def __init__(self, *args, **kwargs):
            self.conf = {}
            self.control = _DummyControl()
            self.Task = _DummyTask

        def task(self, *args, **kwargs):
            def _decorator(fn):
                fn.delay = lambda *a, **k: None
                fn.apply_async = lambda *a, **k: None
                fn.request = SimpleNamespace(retries=0, eta=None)
                return fn

            return _decorator

    celery_stub.Celery = _DummyCelery
    celery_stub.Task = _DummyTask
    sys.modules["celery"] = celery_stub

if "asyncpg" not in sys.modules:
    asyncpg_stub = types.ModuleType("asyncpg")

    class _DummyPool:
        async def close(self):
            return None

        def acquire(self):
            raise RuntimeError("dummy asyncpg pool should not be used in this test")

    class _DummyConnection:
        pass

    async def _create_pool(*args, **kwargs):
        return _DummyPool()

    asyncpg_stub.Pool = _DummyPool
    asyncpg_stub.Connection = _DummyConnection
    asyncpg_stub.create_pool = _create_pool
    sys.modules["asyncpg"] = asyncpg_stub

from services.orchestrator import app as orchestrator_app
from services.orchestrator.schemas import JobState


class _FakeStore:
    def __init__(self, jobs: list[JobState]) -> None:
        self._jobs: dict[str, JobState] = {j.job_id: j for j in jobs}
        self._new_job_seq = 0

    def list_jobs(self) -> list[JobState]:
        return list(self._jobs.values())

    def get(self, job_id: str) -> JobState | None:
        return self._jobs.get(str(job_id))

    def new_job(self, *, request: dict, idempotency_key: str | None):
        del idempotency_key
        self._new_job_seq += 1
        now = time.time()
        job_id = f"new-job-{self._new_job_seq}"
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
            idempotency_key=None,
            request=dict(request or {}),
            result=None,
            error=None,
        )
        self._jobs[job_id] = st
        return st, True

    def set_status(self, job_id: str, status: str, *, stage=None, error=None, result=None):
        st = self._jobs.get(str(job_id))
        if st is None:
            return None
        merged = dict(st.result or {})
        if isinstance(result, dict):
            merged.update(result)
        st2 = JobState(
            job_id=st.job_id,
            status=status,  # type: ignore[arg-type]
            version=int(st.version),
            created_at=st.created_at,
            updated_at=time.time(),
            queued_at=st.queued_at,
            started_at=st.started_at,
            finished_at=st.finished_at,
            stage=stage if stage is not None else st.stage,
            idempotency_key=st.idempotency_key,
            request=dict(st.request or {}),
            result=merged or None,
            error=error if error is not None else st.error,
        )
        self._jobs[job_id] = st2
        return st2

    def patch_request(self, job_id: str, patch: dict):
        st = self._jobs.get(str(job_id))
        if st is None:
            return None
        req = dict(st.request or {})
        req.update(patch or {})
        st2 = JobState(
            job_id=st.job_id,
            status=st.status,
            version=int(st.version),
            created_at=st.created_at,
            updated_at=time.time(),
            queued_at=st.queued_at,
            started_at=st.started_at,
            finished_at=st.finished_at,
            stage=st.stage,
            idempotency_key=st.idempotency_key,
            request=req,
            result=st.result,
            error=st.error,
        )
        self._jobs[job_id] = st2
        return st2


def _job(job_id: str, *, status: str, updated_at: float, project_id: str = "", stage: str = "build") -> JobState:
    return JobState(
        job_id=job_id,
        status=status,  # type: ignore[arg-type]
        version=0,
        created_at=updated_at - 5.0,
        updated_at=updated_at,
        queued_at=updated_at - 4.0,
        started_at=updated_at - 3.0,
        finished_at=None,
        stage=stage,
        idempotency_key=None,
        request={
            "audio_s3_url": "s3://bucket/raw.mp3",
            "project_id": project_id,
            "llm_worker_type": "sdk",
        },
        result=None,
        error=None,
    )


def _build_client(monkeypatch, store: _FakeStore) -> TestClient:
    monkeypatch.setattr(orchestrator_app.JobStore, "from_env", classmethod(lambda cls: store))
    monkeypatch.setattr(
        orchestrator_app,
        "ensure_descriptions_bundle",
        lambda **kwargs: SimpleNamespace(ok=True, action="skip", bundle_path="n/a", reason=""),
    )
    monkeypatch.setattr(orchestrator_app, "ensure_config_initialized", lambda _store: None)
    monkeypatch.setattr(orchestrator_app, "_payment_enabled", False, raising=False)
    app = orchestrator_app.create_app()
    return TestClient(app)


def test_jobs_active_filters_by_age_and_status(monkeypatch) -> None:
    now = time.time()
    store = _FakeStore(
        [
            _job("job-old-running", status="RUNNING", updated_at=now - 1200.0, project_id="tg-1-a"),
            _job("job-fresh-running", status="RUNNING", updated_at=now - 30.0, project_id="tg-2-a"),
            _job("job-failed", status="FAILED", updated_at=now - 5000.0, project_id="tg-3-a"),
        ]
    )
    with _build_client(monkeypatch, store) as client:
        resp = client.get("/jobs/active?min_age_seconds=300&limit=20")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_active"] == 1
    assert len(body["jobs"]) == 1
    assert body["jobs"][0]["job_id"] == "job-old-running"


def test_jobs_batch_returns_requested_jobs_in_order(monkeypatch) -> None:
    now = time.time()
    store = _FakeStore(
        [
            _job("job-1", status="RUNNING", updated_at=now - 120.0, project_id="tg-1-a"),
            _job("job-2", status="FAILED", updated_at=now - 300.0, project_id="tg-2-a"),
        ]
    )
    with _build_client(monkeypatch, store) as client:
        resp = client.post("/jobs/batch", json={"job_ids": ["job-2", "job-1", "job-2"]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert [row["job_id"] for row in body["jobs"]] == ["job-2", "job-1"]


def test_jobs_batch_returns_404_when_any_job_missing(monkeypatch) -> None:
    now = time.time()
    store = _FakeStore([_job("job-1", status="RUNNING", updated_at=now - 120.0, project_id="tg-1-a")])
    with _build_client(monkeypatch, store) as client:
        resp = client.post("/jobs/batch", json={"job_ids": ["job-1", "job-missing"]})

    assert resp.status_code == 404
    body = resp.json()
    assert "jobs not found: job-missing" in body["detail"]


def test_metrics_returns_stage_aware_capacity_snapshot(monkeypatch) -> None:
    now = time.time()
    store = _FakeStore(
        [
            _job("job-build", status="QUEUED", updated_at=now - 30.0, project_id="tg-1-a", stage="build"),
            _job("job-render", status="RUNNING", updated_at=now - 60.0, project_id="tg-2-a", stage="render"),
            _job("job-poll", status="QUEUED", updated_at=now - 90.0, project_id="tg-3-a", stage="render_poll"),
            _job("job-failed", status="FAILED", updated_at=now - 120.0, project_id="tg-4-a", stage="build"),
        ]
    )

    with _build_client(monkeypatch, store) as client:
        resp = client.get("/metrics")

    assert resp.status_code == 200
    body = resp.json()
    assert body["render_backlog"] == 2
    assert body["build_backlog"] == 1
    assert body["job_stage_counts"]["render"] == 1
    assert body["job_stage_counts"]["render_poll"] == 1
    assert body["capacity_policy"]["state"] == "normal"
    assert body["capacity_policy"]["render_backlog_degraded_threshold"] == 100
    assert body["capacity_policy"]["render_backlog_add_windows_node_threshold"] == 300
    assert body["capacity_policy"]["render_node_action"] == "add_3rd_windows_node_manually"
    assert body["queue_topology"]["render_poll_queue_default"] == "render-poll"
    assert body["queue_topology"]["render_poll_split_active"] is True


def test_kill_job_marks_failed_and_returns_revoked_tasks(monkeypatch) -> None:
    now = time.time()
    store = _FakeStore([_job("job-running", status="RUNNING", updated_at=now - 600.0, project_id="tg-99-x")])
    monkeypatch.setattr(orchestrator_app, "_revoke_celery_tasks_for_job", lambda _jid: ["task-1"])

    with _build_client(monkeypatch, store) as client:
        resp = client.post("/jobs/job-running/kill", json={"reason": "manual_kill"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == "job-running"
        assert body["previous_status"] == "RUNNING"
        assert body["new_status"] == "FAILED"
        assert body["stage"] == "admin_kill_stuck"
        assert body["revoked_task_ids"] == ["task-1"]
        assert body["project_id"] == "tg-99-x"

        resp2 = client.post("/jobs/job-running/kill", json={"reason": "repeat"})
        assert resp2.status_code == 409


def test_requeue_active_job_revokes_and_enqueues_without_re_reserve(monkeypatch) -> None:
    now = time.time()
    store = _FakeStore([_job("job-running", status="RUNNING", updated_at=now - 700.0, project_id="tg-11-x")])
    monkeypatch.setattr(orchestrator_app, "_revoke_celery_tasks_for_job", lambda _jid: ["task-run-1"])

    reserve_called = {"count": 0}

    def _unexpected_select(*_args, **_kwargs):
        reserve_called["count"] += 1
        raise AssertionError("select_worker_type must not be called for active requeue")

    monkeypatch.setattr(orchestrator_app, "select_worker_type", _unexpected_select)

    delayed: list[str] = []
    monkeypatch.setattr(orchestrator_app.build_job_sdk, "delay", lambda job_id: delayed.append(str(job_id)))

    with _build_client(monkeypatch, store) as client:
        resp = client.post("/jobs/job-running/requeue", json={"reason": "retry_on_other_node"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "job-running"
    assert body["previous_status"] == "RUNNING"
    assert body["new_status"] == "QUEUED"
    assert body["stage"] == "build"
    assert body["llm_worker_type"] == "sdk"
    assert body["revoked_task_ids"] == ["task-run-1"]
    assert reserve_called["count"] == 0
    assert delayed == ["job-running"]

    st = store.get("job-running")
    assert st is not None
    assert st.status == "QUEUED"
    assert st.stage == "build"
    assert st.request["llm_worker_type"] == "sdk"
    assert st.request["llm_reservation_mode"] == "worker"


def test_requeue_failed_job_selects_worker_and_enqueues(monkeypatch) -> None:
    now = time.time()
    store = _FakeStore([_job("job-failed", status="FAILED", updated_at=now - 800.0, project_id="tg-12-x")])
    monkeypatch.setattr(orchestrator_app, "_revoke_celery_tasks_for_job", lambda _jid: [])
    monkeypatch.setattr(
        orchestrator_app,
        "select_worker_type",
        lambda _store, requested=None: SimpleNamespace(worker_type=str(requested or "sdk")),
    )
    monkeypatch.setattr(
        orchestrator_app,
        "ensure_enqueue_worker_available",
        lambda _store, requested=None: str(requested or ""),
    )

    delayed: list[str] = []
    monkeypatch.setattr(orchestrator_app.build_job_openrouter, "delay", lambda job_id: delayed.append(str(job_id)))

    with _build_client(monkeypatch, store) as client:
        resp = client.post(
            "/jobs/job-failed/requeue",
            json={"reason": "manual_retry", "llm_worker_type": "openrouter"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "job-failed"
    assert body["previous_status"] == "FAILED"
    assert body["new_status"] == "QUEUED"
    assert body["llm_worker_type"] == "openrouter"
    assert delayed == ["job-failed"]

    st = store.get("job-failed")
    assert st is not None
    assert st.status == "QUEUED"
    assert st.stage == "build"
    assert st.request["llm_worker_type"] == "openrouter"
    assert st.request["llm_reservation_mode"] == "worker"


def test_send_audio_rejects_no_gemini_mode(monkeypatch) -> None:
    store = _FakeStore([])
    with _build_client(monkeypatch, store) as client:
        resp = client.post(
            "/send_audio_s3",
            json={
                "audio_s3_url": "s3://bucket/raw/audio.mp3",
                "mode": "no_gemini",
            },
        )

    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"] == "mode=no_gemini is disabled; use mode=with_gemini"


def test_send_audio_rejects_when_enqueue_disabled(monkeypatch) -> None:
    store = _FakeStore([])
    monkeypatch.setattr(
        orchestrator_app,
        "SETTINGS",
        replace(
            orchestrator_app.SETTINGS,
            orchestrator_enqueue_enabled=False,
            orchestrator_node_name="blast-ops-canary",
            system_maintenance_mode=False,
        ),
    )
    with _build_client(monkeypatch, store) as client:
        resp = client.post(
            "/send_audio_s3",
            json={
                "audio_s3_url": "s3://bucket/raw/audio.mp3",
                "mode": "with_gemini",
            },
        )

    assert resp.status_code == 503
    body = resp.json()
    assert body["detail"] == "enqueue disabled on node=blast-ops-canary"


def test_requeue_rejects_when_enqueue_disabled(monkeypatch) -> None:
    now = time.time()
    store = _FakeStore([_job("job-failed", status="FAILED", updated_at=now - 800.0, project_id="tg-12-x")])
    monkeypatch.setattr(
        orchestrator_app,
        "SETTINGS",
        replace(
            orchestrator_app.SETTINGS,
            orchestrator_enqueue_enabled=False,
            orchestrator_node_name="blast-ops-canary",
            system_maintenance_mode=False,
        ),
    )
    with _build_client(monkeypatch, store) as client:
        resp = client.post(
            "/jobs/job-failed/requeue",
            json={"reason": "manual_retry", "llm_worker_type": "openrouter"},
        )

    assert resp.status_code == 503
    body = resp.json()
    assert body["detail"] == "enqueue disabled on node=blast-ops-canary"


def test_send_audio_reuse_inherits_source_routing_and_queue(monkeypatch) -> None:
    now = time.time()
    source = _job("job-source", status="SUCCEEDED", updated_at=now - 60.0, project_id="tg-src")
    source.request.update(
        {
            "origin_node": "orchestrator-0",
            "build_queue": "build.orchestrator-0",
            "render_queue": "render.orchestrator-0",
        }
    )
    store = _FakeStore([source])
    monkeypatch.setattr(
        orchestrator_app,
        "ensure_enqueue_worker_available",
        lambda _store, requested=None: str(requested or ""),
    )

    queued: dict[str, object] = {}

    def _apply_async(*, args=None, queue=None, **kwargs):
        queued["args"] = list(args or [])
        queued["queue"] = queue
        queued["kwargs"] = dict(kwargs or {})
        return None

    monkeypatch.setattr(orchestrator_app.build_job_sdk, "apply_async", _apply_async)
    monkeypatch.setattr(orchestrator_app.build_job_sdk, "delay", lambda *_args, **_kwargs: None)

    with _build_client(monkeypatch, store) as client:
        resp = client.post(
            "/send_audio_s3",
            json={
                "audio_s3_url": "s3://bucket/raw/audio.mp3",
                "mode": "with_gemini",
                "llm_worker_type": "sdk",
                "reuse_text_job_id": "job-source",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    new_job_id = str(body["job_id"])
    st = store.get(new_job_id)
    assert st is not None
    assert st.request.get("origin_node") == "orchestrator-0"
    assert st.request.get("build_queue") == "build.orchestrator-0"
    assert st.request.get("render_queue") == "render.orchestrator-0"
    assert st.request.get("render_poll_queue") == "render-poll.orchestrator-0"
    assert queued == {
        "args": [new_job_id],
        "queue": "build.orchestrator-0",
        "kwargs": {},
    }


def test_send_audio_reuse_missing_source_returns_409(monkeypatch) -> None:
    store = _FakeStore([])
    with _build_client(monkeypatch, store) as client:
        resp = client.post(
            "/send_audio_s3",
            json={
                "audio_s3_url": "s3://bucket/raw/audio.mp3",
                "mode": "with_gemini",
                "reuse_text_job_id": "missing-source-job",
            },
        )

    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"] == "reuse_text_source_job_not_found: missing-source-job"


def test_send_audio_queue_first_accepts_generic_worker_and_uses_generic_build_task(monkeypatch) -> None:
    store = _FakeStore([])
    monkeypatch.setattr(
        orchestrator_app,
        "ensure_enqueue_worker_available",
        lambda _store, requested=None: str(requested or ""),
    )

    delayed: list[str] = []
    monkeypatch.setattr(orchestrator_app.build_job, "delay", lambda job_id: delayed.append(str(job_id)))

    with _build_client(monkeypatch, store) as client:
        resp = client.post(
            "/send_audio_s3",
            json={
                "audio_s3_url": "s3://bucket/raw/audio.mp3",
                "mode": "with_gemini",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    new_job_id = str(body["job_id"])
    assert delayed == [new_job_id]

    st = store.get(new_job_id)
    assert st is not None
    assert st.status == "QUEUED"
    assert st.request.get("llm_worker_type") in {None, ""}
    assert st.request.get("llm_reservation_mode") == "worker"
    assert st.request.get("render_poll_queue") == "render-poll"


def test_send_audio_rejects_when_no_enabled_worker_types(monkeypatch) -> None:
    store = _FakeStore([])
    monkeypatch.setattr(
        orchestrator_app,
        "ensure_enqueue_worker_available",
        lambda _store, requested=None: (_ for _ in ()).throw(RuntimeError("llm_workers_no_enabled_types")),
    )

    with _build_client(monkeypatch, store) as client:
        resp = client.post(
            "/send_audio_s3",
            json={
                "audio_s3_url": "s3://bucket/raw/audio.mp3",
                "mode": "with_gemini",
            },
        )

    assert resp.status_code == 503
    body = resp.json()
    assert "llm_workers_no_enabled_types" in body["detail"]
