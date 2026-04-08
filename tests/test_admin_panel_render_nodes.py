from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import unquote_plus

from fastapi.testclient import TestClient

from services.tg_bot_public.admin_panel import build_app


class _DummyCreditsDB:
    pass


class _DummyStateStore:
    pass


def _build_client(*, enabled: bool) -> TestClient:
    settings = SimpleNamespace(
        admin_panel_password="secret",
        orchestrator_public_url="",
        windows_donor_url="",
        admin_panel_enable_donor_restart=enabled,
        twc_token="",
    )
    app = build_app(
        credits_db=_DummyCreditsDB(),  # type: ignore[arg-type]
        state_store=_DummyStateStore(),  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
        tbank_client=None,
        bot_ref=None,
    )
    return TestClient(app)


def test_render_nodes_page_renders_when_restart_disabled() -> None:
    with _build_client(enabled=False) as client:
        resp = client.get("/admin/render-nodes", auth=("admin", "secret"))
    assert resp.status_code == 200
    assert "Donor restart control" in resp.text


def test_restart_donor_returns_error_when_feature_disabled() -> None:
    with _build_client(enabled=False) as client:
        resp = client.post(
            "/admin/render-nodes/restart-donor",
            auth=("admin", "secret"),
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert "/admin/render-nodes?err=" in str(resp.headers.get("location") or "")


def test_restart_donor_returns_validation_error_when_config_missing() -> None:
    with _build_client(enabled=True) as client:
        resp = client.post(
            "/admin/render-nodes/restart-donor",
            auth=("admin", "secret"),
            follow_redirects=False,
        )
    assert resp.status_code == 303
    location = str(resp.headers.get("location") or "")
    assert "/admin/render-nodes?err=" in location
    err_msg = unquote_plus(location.split("err=", 1)[-1])
    assert "WINDOWS_DONOR_HOST" in err_msg or "WINDOWS_DONOR_PASSWORD" in err_msg
