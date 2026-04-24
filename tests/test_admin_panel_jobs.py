from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import unquote_plus

from fastapi.testclient import TestClient

from services.tg_bot_public import admin_panel


class _DummyCreditsDB:
    pass


class _DummyPoolAwareCreditsDB:
    def _pool_or_fail(self):
        return object()


class _DummyStateStore:
    pass


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = int(status_code)
        self._payload = dict(payload)
        self.text = json.dumps(self._payload, ensure_ascii=False)

    def json(self) -> dict:
        return dict(self._payload)


class _FakeAsyncClient:
    last_post_url: str = ""
    last_post_json: dict | None = None

    def __init__(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    async def get(self, url: str, params=None):
        _ = params
        if url.endswith("/metrics"):
            return _FakeResponse(
                200,
                {
                    "job_status_counts": {"NEW": 0, "QUEUED": 5, "RUNNING": 2, "SUCCEEDED": 10, "FAILED": 1},
                    "job_stage_counts": {"build": 3, "render": 4},
                    "render_backlog": 4,
                    "build_backlog": 3,
                    "capacity_policy": {
                        "state": "degraded",
                        "reason_codes": ["render_backlog_high", "llm_saturation"],
                        "operator_action": "Render backlog is elevated: watch queue and prepare to add the 3rd Windows node at 300.",
                        "user_message": "Сейчас есть очередь, поэтому обработка может идти медленнее обычного, но заявка уже принята.",
                        "render_backlog_degraded_threshold": 100,
                        "render_backlog_add_windows_node_threshold": 300,
                        "build_backlog_degraded_threshold": 30,
                        "build_backlog_manual_maintenance_threshold": 80,
                        "render_node_action": "add_3rd_windows_node_manually",
                    },
                    "queue_topology": {
                        "render_queue_default": "render",
                        "render_poll_queue_default": "render-poll",
                        "render_poll_split_active": True,
                    },
                },
            )
        if url.endswith("/windows-nodes"):
            return _FakeResponse(
                200,
                {
                    "effective_urls": ["http://win-1:8000", "http://win-2:8000"],
                    "runtime_urls": ["http://win-1:8000", "http://win-2:8000"],
                },
            )
        if url.endswith("/llm-workers"):
            return _FakeResponse(
                200,
                {
                    "workers": {
                        "vertex_sdk_mix": {
                            "enabled": True,
                            "inflight": 7,
                            "max_inflight": 100,
                            "available_slots": 93,
                        }
                    }
                },
            )
        if "api.telegram.org" in url and url.endswith("/getWebhookInfo"):
            return _FakeResponse(
                200,
                {
                    "ok": True,
                    "result": {
                        "url": "https://blast808.com/telegram/webhook",
                        "pending_update_count": 0,
                    },
                },
            )
        if url.endswith("/jobs/job-failed"):
            return _FakeResponse(
                200,
                {
                    "job_id": "job-failed",
                    "status": "FAILED",
                    "version": 3,
                    "created_at": 1776880000.0,
                    "updated_at": 1776880100.0,
                    "queued_at": None,
                    "started_at": None,
                    "finished_at": None,
                    "stage": "build",
                    "idempotency_key": "idem-job-failed",
                    "request": {
                        "project_id": "tg-975769043-demo",
                        "llm_worker_type": "vertex_sdk_mix",
                    },
                    "result": {"llm_worker_type": "vertex_sdk_mix"},
                    "error": "resume_missing",
                },
            )
        raise AssertionError(f"unexpected GET {url}")

    async def post(self, url: str, json=None):
        _FakeAsyncClient.last_post_url = str(url)
        _FakeAsyncClient.last_post_json = dict(json or {})
        if "api.telegram.org" in url and url.endswith("/sendMessage"):
            return _FakeResponse(200, {"ok": True, "result": {"message_id": 42}})
        if url.endswith("/jobs/job-failed/requeue"):
            payload = dict(json or {})
            return _FakeResponse(
                200,
                {
                    "job_id": "job-failed",
                    "previous_status": "FAILED",
                    "new_status": "QUEUED",
                    "stage": "build",
                    "reason": str(payload.get("reason") or ""),
                    "llm_worker_type": str(payload.get("llm_worker_type") or "vertex_sdk_mix"),
                    "revoked_task_ids": [],
                    "project_id": "tg-975769043-demo",
                },
            )
        raise AssertionError(f"unexpected POST {url}")


class _FakeRuntimeStore:
    def __init__(self, _pool) -> None:
        _ = _pool

    async def list_runs(self, *, surface: str, status: str = "", include_terminal: bool = True, limit: int = 200, offset: int = 0):
        _ = (surface, status, include_terminal, limit, offset)
        return [
            {
                "run_id": "run-public-1",
                "surface": "public",
                "chat_id": 975769043,
                "batch_id": "batch-xyz",
                "status": "running",
                "versions_total": 3,
                "next_version_to_enqueue": 2,
                "current_stage": "render",
                "last_error_code": "",
                "last_error_text": "",
                "created_at": "2026-04-22T20:00:00+00:00",
                "updated_at": "2026-04-22T20:05:00+00:00",
            }
        ]

    async def get_run(self, run_id: str):
        if run_id != "run-public-1":
            return {}
        return {
            "run_id": "run-public-1",
            "surface": "public",
            "chat_id": 975769043,
            "batch_id": "batch-xyz",
            "status": "running",
            "versions_total": 3,
            "next_version_to_enqueue": 2,
            "current_stage": "render",
            "last_error_code": "",
            "last_error_text": "",
            "created_at": "2026-04-22T20:00:00+00:00",
            "updated_at": "2026-04-22T20:05:00+00:00",
        }

    async def get_versions(self, run_id: str):
        _ = run_id
        return [
            {
                "version_index": 1,
                "job_id": "job-failed",
                "job_status": "FAILED",
                "job_stage": "build",
                "worker_type": "vertex_sdk_mix",
                "origin_node": "orchestrator-0",
                "build_queue": "build.orchestrator-0",
                "render_queue": "render.orchestrator-0",
                "last_error_text": "resume_missing",
            }
        ]

    async def list_outbox_items(self, *, surface: str, run_id: str = "", status=None, kind: str = "", limit: int = 200):
        _ = (surface, run_id, status, kind, limit)
        return [
            {
                "dedupe_key": "public:run-public-1:telegram_video_delivery:job-failed",
                "kind": "telegram_video_delivery",
                "status": "failed",
                "attempt_count": 2,
                "next_attempt_at": "2026-04-22T20:06:00+00:00",
                "sent_at": None,
                "last_error": "telegram timeout",
            }
        ]

    async def list_events(self, run_id: str, *, event_type: str = "", limit: int = 500):
        _ = (run_id, event_type, limit)
        return [
            {
                "created_at": "2026-04-22T20:01:00+00:00",
                "event_type": "version_succeeded",
                "job_id": "job-failed",
                "payload": {"version_index": 1},
            }
        ]

    async def get_runtime_stats(self, *, surface: str):
        _ = surface
        return {
            "run_status_counts": {"running": 1},
            "outbox_status_counts": {"failed": 1},
            "outbox_oldest_due_age_s": {"failed": 120},
            "old_incomplete_runs_by_stage": {"render": 1},
        }


def _build_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(admin_panel.httpx, "AsyncClient", _FakeAsyncClient)
    settings = SimpleNamespace(
        admin_panel_password="secret",
        orchestrator_public_url="http://orchestrator",
        dozzle_base_url="",
        tg_delivery_mode="webhook",
        tg_bot_token="public-token",
        alert_telegram_bot_token="alert-token",
        alert_telegram_chat_id="975769043",
    )
    app = admin_panel.build_app(
        credits_db=_DummyCreditsDB(),  # type: ignore[arg-type]
        state_store=_DummyStateStore(),  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
        tbank_client=None,
        bot_ref=None,
    )
    return TestClient(app)


def _build_client_with_runtime(monkeypatch) -> TestClient:
    monkeypatch.setattr(admin_panel.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(admin_panel, "GenerationRuntimeStore", _FakeRuntimeStore)
    settings = SimpleNamespace(
        admin_panel_password="secret",
        orchestrator_public_url="http://orchestrator",
        dozzle_base_url="",
        tg_delivery_mode="webhook",
        tg_bot_token="public-token",
        alert_telegram_bot_token="alert-token",
        alert_telegram_chat_id="975769043",
    )
    app = admin_panel.build_app(
        credits_db=_DummyPoolAwareCreditsDB(),  # type: ignore[arg-type]
        state_store=_DummyStateStore(),  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
        tbank_client=None,
        bot_ref=None,
    )
    return TestClient(app)


def test_job_detail_page_shows_requeue_action_for_failed_job(monkeypatch) -> None:
    with _build_client(monkeypatch) as client:
        resp = client.get("/admin/jobs/job-failed", auth=("admin", "secret"))

    assert resp.status_code == 200
    assert "/admin/jobs/job-failed/requeue" in resp.text
    assert "Requeue" in resp.text
    assert "resume_missing" in resp.text


def test_job_detail_requeue_redirects_with_ok_message(monkeypatch) -> None:
    with _build_client(monkeypatch) as client:
        resp = client.post(
            "/admin/jobs/job-failed/requeue",
            auth=("admin", "secret"),
            data={
                "reason": "manual_retry",
                "llm_worker_type": "vertex_sdk_mix",
                "min_age_seconds": "0",
                "limit": "200",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 303
    location = str(resp.headers.get("location") or "")
    assert "/admin/jobs?" in location
    assert "ok=" in location
    assert "requeued job=job-failed" in unquote_plus(location)
    assert _FakeAsyncClient.last_post_url.endswith("/jobs/job-failed/requeue")
    assert _FakeAsyncClient.last_post_json == {
        "reason": "manual_retry by admin",
        "llm_worker_type": "vertex_sdk_mix",
    }


def test_runs_page_shows_runtime_run_rows(monkeypatch) -> None:
    with _build_client_with_runtime(monkeypatch) as client:
        resp = client.get("/admin/runs", auth=("admin", "secret"))

    assert resp.status_code == 200
    assert "run-public-1" in resp.text
    assert "batch-xyz" in resp.text
    assert "render" in resp.text


def test_run_detail_page_shows_versions_outbox_and_events(monkeypatch) -> None:
    with _build_client_with_runtime(monkeypatch) as client:
        resp = client.get("/admin/runs/run-public-1", auth=("admin", "secret"))

    assert resp.status_code == 200
    assert "job-failed" in resp.text
    assert "telegram_video_delivery" in resp.text
    assert "version_succeeded" in resp.text


def test_ops_page_shows_operator_toolkit(monkeypatch) -> None:
    with _build_client_with_runtime(monkeypatch) as client:
        resp = client.get("/admin/ops", auth=("admin", "secret"))

    assert resp.status_code == 200
    assert "Admin-only Smoke Checks" in resp.text
    assert "Friendly Error Smoke" in resp.text
    assert "render backlog" in resp.text
    assert "Backpressure thresholds" in resp.text
    assert "render-poll" in resp.text
    assert "Сейчас есть очередь" in resp.text
    assert "vertex_sdk_mix" in resp.text
    assert "Outbox statuses" in resp.text


def test_ops_alert_smoke_posts_to_alert_bot(monkeypatch) -> None:
    with _build_client_with_runtime(monkeypatch) as client:
        resp = client.post("/admin/ops/alert-smoke", auth=("admin", "secret"), follow_redirects=False)

    assert resp.status_code == 303
    assert "/admin/ops?ok=" in str(resp.headers.get("location") or "")
    assert "api.telegram.org/botalert-token/sendMessage" in _FakeAsyncClient.last_post_url
    assert _FakeAsyncClient.last_post_json is not None
    assert _FakeAsyncClient.last_post_json["chat_id"] == "975769043"
    assert "Blast admin alert smoke" in _FakeAsyncClient.last_post_json["text"]


def test_ops_friendly_error_smoke_keeps_user_text_safe_and_alerts_tech_details(monkeypatch) -> None:
    with _build_client_with_runtime(monkeypatch) as client:
        resp = client.post("/admin/ops/friendly-error-smoke", auth=("admin", "secret"), follow_redirects=False)

    assert resp.status_code == 303
    assert "/admin/ops?ok=" in str(resp.headers.get("location") or "")
    assert _FakeAsyncClient.last_post_json is not None
    text = str(_FakeAsyncClient.last_post_json["text"])
    assert "Blast friendly-error smoke" in text
    assert "user_text:" in text
    assert "traceback stays in ops alert only" in text
