"""Сырые ошибки рендера (трейсбеки, пути ноды) не уходят обычным пользователям."""
from __future__ import annotations

import importlib
import sys

import pytest

from tests.test_web_asr_preview import _env

TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "C:\\ae_jobs\\9f1c\\app\\run.py", line 88, in poll\n'
    "RuntimeError: Timeout waiting for initial status render_id=abc123"
)


def _errors(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    sys.modules.pop("app.job_errors", None)
    return importlib.import_module("app.job_errors")


@pytest.mark.parametrize(
    ("error", "stage", "kind"),
    (
        (TRACEBACK, "poll", "timeout"),
        ("render poll_404 render_id=x", "poll", "lost"),
        ("dispatch_503 queue full", "dispatch", "busy"),
        ("footage file not found: C:\\ae_jobs\\x\\media\\video\\a.mp4", "render", "footage"),
        ("ALIGNMENT_UNSUPPORTED_TEXT: reference word 'baby,give'", "build", "lyrics"),
        ("previous variation failed", "skipped", "previous"),
        ("Job cancelled by operator", "build", "cancelled"),
        ("stage2_style_rotation_missing_artist_id", "build", "source"),
        ("orchestrator does not support montage table edits", "queued", "service"),
        ("AfterFX exited with code 1", "render", "render"),
        ("render failed", "poll", "render"),
        ("something odd", "build", "unknown"),
        (None, None, "unknown"),
    ),
)
def test_failure_kind_by_error_text(monkeypatch: pytest.MonkeyPatch, error, stage, kind) -> None:
    assert _errors(monkeypatch).failure_kind(error, stage) == kind


def test_public_job_hides_raw_text_for_users_and_keeps_live_job(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = _errors(monkeypatch)
    live = {
        "id": "j1", "error": TRACEBACK, "enqueueError": "orchestrator 500",
        "videos": [
            {"index": 1, "status": "COMPLETED", "error": None},
            {"index": 2, "status": "FAILED", "stage": "poll", "error": TRACEBACK},
        ],
    }
    public = errors.public_job(live, admin=False)
    assert public["error"] is None and public["enqueueError"] is None
    assert public["videos"][1]["error"] is None
    assert public["videos"][1]["failureKind"] == "timeout"
    assert "failureKind" not in public["videos"][0]
    # живой джоб воркера не тронут — ошибка нужна ему и в БД
    assert live["videos"][1]["error"] == TRACEBACK and "failureKind" not in live["videos"][1]

    admin = errors.public_job(live, admin=True)
    assert admin["videos"][1]["error"] == TRACEBACK
    assert admin["videos"][1]["failureKind"] == "timeout"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    from fastapi.testclient import TestClient

    with TestClient(main.app) as tc:
        yield tc, main


def test_job_and_project_endpoints_sanitize_for_non_admin(client, monkeypatch: pytest.MonkeyPatch) -> None:
    tc, main = client
    project = main.store.create_project("p", "TRIAL", "auto")
    video = {"id": "j_err_v1", "index": 1, "status": "FAILED", "stage": "poll", "progress": 90, "error": TRACEBACK}
    job = {"id": "j_err", "projectId": project["id"], "userId": main.store.current_user_id(), "status": "FAILED",
           "createdAt": "2026-10-01T00:00:00+00:00", "versions": 1, "videos": [video], "error": TRACEBACK}
    main.store.JOBS[job["id"]] = job

    monkeypatch.setattr(main, "ADMIN_USER_IDS", {"someone-else"})
    for body in (tc.get(f"/api/jobs/{job['id']}").json()["job"],
                 next(j for j in tc.get(f"/api/projects/{job['projectId']}").json()["project"]["jobs"] if j["id"] == job["id"])):
        failed = body["videos"][0]
        assert failed["error"] is None
        assert failed["failureKind"] == "timeout"
        assert "ae_jobs" not in str(body)
    assert tc.get("/api/me").json()["isAdmin"] is False
    assert video["error"] == TRACEBACK  # в store ошибка осталась

    monkeypatch.setattr(main, "ADMIN_USER_IDS", {main.store.current_user_id()})
    body = tc.get(f"/api/jobs/{job['id']}").json()["job"]
    assert body["videos"][0]["error"] == TRACEBACK
    assert body["videos"][0]["failureKind"] == "timeout"
