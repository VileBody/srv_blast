from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

os.environ.setdefault("MODE", "dev")
os.environ.setdefault("BLAST_BACKEND_MODE", "mock")
os.environ.setdefault("APP_URL", "http://localhost:5173")
os.environ.setdefault("BLAST_SESSION_SECRET", "test-session-secret")
os.environ.setdefault("BLAST_CORS_ORIGINS", "http://localhost:5173")

from web_app.backend.app import analytics, main, persistence, security


def _event(name: str, user_id: str, **props: object) -> dict[str, object]:
    return {
        "id": f"{name}-{user_id}-{len(analytics.EVENTS)}",
        "name": name,
        "userId": user_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "props": props,
    }


def test_web_product_metrics_group_pages_stages_and_actions(monkeypatch) -> None:
    monkeypatch.setattr(persistence, "save_event", lambda event: None)
    monkeypatch.setattr(
        analytics,
        "EVENTS",
        [
            _event("page_view", "u1", route="dashboard"),
            _event("app_entry", "u1", source="telegram", medium="social", campaign="release"),
            _event("app_entry", "u2", source="telegram", medium="social", campaign="release"),
            _event("page_view", "u1", route="wizard"),
            _event("page_view", "u2", route="wizard"),
            _event("wizard_stage_view", "u1", stage=2),
            _event("wizard_stage_view", "u1", stage=1),
            _event("wizard_stage_view", "u2", stage=1),
            _event("video_previewed", "u1", videoId="v1"),
        ],
    )

    result = analytics.web_product_metrics(30)

    assert result["pages"][0] == {"route": "wizard", "events": 2, "users": 2}
    assert result["attribution"][0] == {
        "source": "telegram", "medium": "social", "campaign": "release", "events": 2, "users": 2
    }
    assert [row["stage"] for row in result["wizardStages"]] == ["1", "2"]
    assert result["actions"] == [{"name": "video_previewed", "events": 1, "users": 1}]


def test_track_once_deduplicates_reconciled_milestone(monkeypatch) -> None:
    saved: list[dict[str, object]] = []
    monkeypatch.setattr(persistence, "save_event", saved.append)
    monkeypatch.setattr(analytics, "EVENTS", [])

    first = analytics.track_once("generation_completed", "u1", "job:j1:completed", {"videos": 3})
    second = analytics.track_once("generation_completed", "u1", "job:j1:completed", {"videos": 3})

    assert first is second
    assert len(saved) == 1
    assert len(analytics.EVENTS) == 1


def test_channel_snapshot_keeps_identity_sets_for_cross_channel_dedupe(monkeypatch) -> None:
    monkeypatch.setattr(
        analytics,
        "EVENTS",
        [
            _event("app_entry", "u1"),
            _event("generation_started", "u1"),
            _event("generation_started", "u1"),
            _event("generation_started", "u2"),
        ],
    )

    result = analytics.channel_snapshot(30)

    assert result["activeUserIds"] == {"u1", "u2"}
    assert result["events"]["generation_started"]["events"] == 3
    assert result["events"]["generation_started"]["userIds"] == {"u1", "u2"}


def test_track_once_retries_after_persistence_failure(monkeypatch) -> None:
    attempts = 0

    def save_event(event: dict[str, object]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(persistence, "save_event", save_event)
    monkeypatch.setattr(analytics, "EVENTS", [])

    try:
        analytics.track_once("plan_purchased", "u1", "payment:p1:confirmed")
    except RuntimeError:
        pass

    analytics.track_once("plan_purchased", "u1", "payment:p1:confirmed")
    assert attempts == 2
    assert len(analytics.EVENTS) == 1


def test_production_admin_endpoint_fails_closed_without_allowlist(monkeypatch) -> None:
    monkeypatch.setattr(main, "RUNTIME", SimpleNamespace(production=True))
    monkeypatch.setattr(main, "ADMIN_USER_IDS", set())

    with pytest.raises(HTTPException) as caught:
        main._require_admin()

    assert caught.value.status_code == 403


def test_browser_cannot_forge_server_analytics_event() -> None:
    with pytest.raises(HTTPException) as caught:
        main.api_track(main.TrackPayload(name="plan_purchased", props={"tier": "BLAST"}))

    assert caught.value.status_code == 422


def test_browser_analytics_rejects_unknown_properties() -> None:
    with pytest.raises(HTTPException) as caught:
        main.api_track(main.TrackPayload(name="page_view", props={"email": "private@example.com"}))

    assert caught.value.status_code == 422


def test_rate_limit_identity_ignores_spoofable_forwarded_chain() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/tg-start",
            "headers": [
                (b"x-forwarded-for", b"198.51.100.1, 203.0.113.5"),
                (b"x-real-ip", b"203.0.113.5"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )

    assert security._client_key(request) == "203.0.113.5"
