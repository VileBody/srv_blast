from __future__ import annotations

import urllib.error

import pytest

from services.orchestrator.windows_client import (
    WindowsRenderClient,
    normalize_windows_render_api_mode,
)


def test_normalize_windows_render_api_mode_accepts_known_values() -> None:
    assert normalize_windows_render_api_mode("render") == "render"
    assert normalize_windows_render_api_mode("jobs") == "jobs"
    assert normalize_windows_render_api_mode("  RENDER ") == "render"


def test_normalize_windows_render_api_mode_rejects_unknown_value() -> None:
    with pytest.raises(RuntimeError):
        normalize_windows_render_api_mode("legacy")


def test_dispatch_render_uses_render_endpoint_in_render_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def _fake_post(url: str, payload: dict, *, timeout_s: float):
        _ = (payload, timeout_s)
        seen.append(url)
        return {"status": "accepted", "render_id": "rid_1"}

    monkeypatch.setattr("services.orchestrator.windows_client._post_json", _fake_post)
    c = WindowsRenderClient("http://win:8000", timeout_s=7.0, api_mode="render")

    out = c.dispatch_render({"job_id": "j1"})

    assert seen == ["http://win:8000/render"]
    assert out["_api"] == "render"
    assert out["render_id"] == "rid_1"


def test_dispatch_render_uses_jobs_endpoint_in_jobs_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def _fake_post(url: str, payload: dict, *, timeout_s: float):
        _ = (payload, timeout_s)
        seen.append(url)
        return {"success": True, "job_id": "j1"}

    monkeypatch.setattr("services.orchestrator.windows_client._post_json", _fake_post)
    c = WindowsRenderClient("http://win:8000", timeout_s=7.0, api_mode="jobs")

    out = c.dispatch_render({"job_id": "j1"})

    assert seen == ["http://win:8000/jobs"]
    assert out["_api"] == "jobs"
    assert out["success"] is True


def test_dispatch_render_does_not_fallback_between_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_post(url: str, payload: dict, *, timeout_s: float):
        _ = (url, payload, timeout_s)
        raise urllib.error.HTTPError(url, 404, "not found", hdrs=None, fp=None)

    monkeypatch.setattr("services.orchestrator.windows_client._post_json", _fail_post)
    c = WindowsRenderClient("http://win:8000", api_mode="render")

    with pytest.raises(urllib.error.HTTPError):
        c.dispatch_render({"job_id": "j1"})
