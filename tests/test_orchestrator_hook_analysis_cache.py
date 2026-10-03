"""`/hook/analyze` (шаг FX) и `/storyboard/cuts` («Пул») делят один анализ окна трека:
второй вызов на тот же трек+окно не скачивает и не анализирует трек заново."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from services.orchestrator import storyboard_api


def _analysis():
    beats = [round(0.5 * i, 3) for i in range(41)]
    return SimpleNamespace(
        bpm=120.0,
        beats=beats,
        onsets_classified=[SimpleNamespace(t=b, type="kick", confidence=0.5) for b in beats[1:]],
        drop_candidates=[SimpleNamespace(t=8.0, confidence=0.9, snapped_to_beat=True, source="energy")],
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float, float]]:
    seen: list[tuple[str, float, float]] = []

    def fake(*, audio_s3_url, clip_start_abs, clip_end_abs):
        seen.append((audio_s3_url, clip_start_abs, clip_end_abs))
        return _analysis()

    monkeypatch.setattr(storyboard_api, "_analyze_window_uncached", fake)
    monkeypatch.setattr(storyboard_api, "_ANALYSIS_CACHE", storyboard_api.OrderedDict())
    return seen


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # приложение оркестратора тянет prometheus_client/celery — в CI есть, в веб-venv нет
    pytest.importorskip("prometheus_client")
    from services.orchestrator import app as orchestrator_app
    from tests.test_orchestrator_health_readiness import _FakeStore, _settings

    monkeypatch.setattr(orchestrator_app, "SETTINGS", _settings())
    monkeypatch.setattr(orchestrator_app, "create_asset_router", lambda: APIRouter())
    monkeypatch.setattr(orchestrator_app.JobStore, "from_env", classmethod(lambda cls: _FakeStore()))
    monkeypatch.setattr(orchestrator_app, "ensure_config_initialized", lambda store: None)
    monkeypatch.setattr(
        orchestrator_app,
        "ensure_descriptions_bundle",
        lambda **_: SimpleNamespace(ok=True, action="ok", bundle_path="bundle", reason=""),
    )
    return TestClient(orchestrator_app.create_app())


def test_fx_drops_and_pool_cuts_share_one_analysis(monkeypatch: pytest.MonkeyPatch, calls) -> None:
    client = _client(monkeypatch)
    window = {"audio_s3_url": "s3://raw/track.mp3", "clip_start_sec": 0.0, "clip_end_sec": 20.0}

    drops = client.post("/hook/analyze", json=window)
    assert drops.status_code == 200, drops.text
    assert drops.json()["drop_candidates"][0]["t"] == 8.0

    cuts = client.post("/storyboard/cuts", json={**window, "user_drop_t": None})
    assert cuts.status_code == 200, cuts.text
    assert cuts.json()["bpm"] == 120.0 and cuts.json()["drop_t"] == 8.0
    assert calls == [("s3://raw/track.mp3", 0.0, 20.0)]


def test_cache_is_keyed_by_track_and_exact_window(calls) -> None:
    storyboard_api.analyze_window(audio_s3_url="s3://raw/a.mp3", clip_start_abs=0.0, clip_end_abs=20.0)
    storyboard_api.analyze_window(audio_s3_url="s3://raw/a.mp3", clip_start_abs=0.0, clip_end_abs=20.0)
    storyboard_api.analyze_window(audio_s3_url="s3://raw/a.mp3", clip_start_abs=0.0, clip_end_abs=20.5)
    storyboard_api.analyze_window(audio_s3_url="s3://raw/b.mp3", clip_start_abs=0.0, clip_end_abs=20.0)
    assert len(calls) == 3


def test_cached_result_is_a_copy(calls) -> None:
    first = storyboard_api.analyze_window(audio_s3_url="s3://raw/a.mp3", clip_start_abs=0.0, clip_end_abs=20.0)
    first.beats.clear()
    again = storyboard_api.analyze_window(audio_s3_url="s3://raw/a.mp3", clip_start_abs=0.0, clip_end_abs=20.0)
    assert len(again.beats) == 41 and len(calls) == 1


def test_failures_are_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storyboard_api, "_ANALYSIS_CACHE", storyboard_api.OrderedDict())
    attempts = []

    def flaky(**_kw):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("s3 down")
        return _analysis()

    monkeypatch.setattr(storyboard_api, "_analyze_window_uncached", flaky)
    with pytest.raises(RuntimeError):
        storyboard_api.analyze_window(audio_s3_url="s3://raw/a.mp3", clip_start_abs=0.0, clip_end_abs=20.0)
    assert storyboard_api.analyze_window(audio_s3_url="s3://raw/a.mp3", clip_start_abs=0.0, clip_end_abs=20.0).bpm == 120.0
    assert len(attempts) == 2
