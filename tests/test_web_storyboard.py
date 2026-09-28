"""Раскадровка на сайте: мок склеек/подбора, ручки /api/wizard/storyboard/*,
привязка планов к вариациям и проводка footage_plan в оркестратор."""
from __future__ import annotations

import importlib
import sys
from typing import Any

import pytest

from tests.test_web_asr_preview import _env
from tests.test_web_production_backend import _Response, _backend, _config, _job, _module


def _sb(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    sys.modules.pop("app.storyboard", None)
    return importlib.import_module("app.storyboard")


VIBES = [
    {"id": "night-city", "name": "Ночной город", "previewUrl": "https://cdn/night.mp4"},
    {"id": "neon", "name": "Неон", "previewUrl": "https://cdn/neon.mp4"},
]


# ── mock ──────────────────────────────────────────────────────────────────────

def test_mock_cuts_scale_around_auto_and_cut_on_the_drop(monkeypatch) -> None:
    sb = _sb(monkeypatch)
    res = sb.mock_cuts(start=10.0, end=25.0, drop=15.0)
    counts = [len(res["cuts"][p]) for p in ("sparse", "auto", "dense")]
    assert counts[0] < counts[1] < counts[2]
    assert all(15.0 in res["cuts"][p] for p in ("sparse", "auto", "dense"))
    assert all(10.0 < c < 25.0 for c in res["cuts"]["dense"])


def test_mock_pick_does_not_repeat_clips_inside_a_vibe(monkeypatch) -> None:
    sb = _sb(monkeypatch)
    out = sb.mock_pick(vibes=VIBES, start=0.0, end=10.0, cuts=[2.0, 4.0, 6.0, 8.0], videos=[
        {"index": 1, "group": "Ночной город", "seedKey": "a"},
        {"index": 2, "group": "Ночной город", "seedKey": "b"},
        {"index": 3, "group": "Неон", "seedKey": "c"},
    ])
    night = [c["fileName"] for v in out["videos"][:2] for c in v["clips"]]
    assert len(night) == 10 and len(set(night)) == 10
    assert out["videos"][2]["clips"][0]["previewUrl"] == "https://cdn/neon.mp4"
    plan = out["videos"][0]["plan"]
    assert plan["switch_points_abs"] == [2.0, 4.0, 6.0, 8.0] and len(plan["clips"]) == 5


def test_mock_pick_keeps_pins(monkeypatch) -> None:
    sb = _sb(monkeypatch)
    out = sb.mock_pick(vibes=VIBES, start=0.0, end=10.0, cuts=[5.0], videos=[
        {"index": 1, "group": "Неон", "seedKey": "a", "pins": {1: "neon-007.mp4"}},
    ])
    assert out["videos"][0]["clips"][1]["fileName"] == "neon-007.mp4"
    assert out["videos"][0]["plan"]["clips"][1]["file_name"] == "neon-007.mp4"


def test_mock_pick_unknown_vibe_is_an_error(monkeypatch) -> None:
    sb = _sb(monkeypatch)
    with pytest.raises(sb.StoryboardError):
        sb.mock_pick(vibes=VIBES, start=0.0, end=10.0, cuts=[], videos=[{"index": 1, "group": "Закат", "seedKey": "a"}])


# ── привязка к вариациям ──────────────────────────────────────────────────────

def _variation(index: int, group: str) -> dict[str, Any]:
    return {"index": index, "background": {"mode": "footage", "groups": [group], "sourceAssets": []}}


def _entry(index: int, group: str, start=10.0, end=20.0) -> dict[str, Any]:
    return {"index": index, "group": group, "plan": {"version": 1, "clip_start_abs": start, "clip_end_abs": end,
                                                      "switch_points_abs": [], "clips": [{"file_name": "a.mp4"}]}}


TL = {"cuts": []}


def test_attach_puts_each_plan_on_its_variation(monkeypatch) -> None:
    sb = _sb(monkeypatch)
    variations = [_variation(1, "Неон"), _variation(2, "Ночной город")]
    sb.attach_to_variations(variations, {"videos": [_entry(2, "Ночной город")]}, {"from": 10.0, "to": 20.0}, TL)
    assert "footagePlan" not in variations[0]["background"]
    assert variations[1]["background"]["footagePlan"]["clips"][0]["file_name"] == "a.mp4"


def test_attach_refuses_a_plan_for_another_background(monkeypatch) -> None:
    sb = _sb(monkeypatch)
    with pytest.raises(sb.StoryboardError, match="фон"):
        sb.attach_to_variations([_variation(1, "Неон")], {"videos": [_entry(1, "Ночной город")]}, {"from": 10.0, "to": 20.0}, TL)


def test_attach_refuses_a_plan_for_another_window(monkeypatch) -> None:
    sb = _sb(monkeypatch)
    with pytest.raises(sb.StoryboardError, match="отрывка"):
        sb.attach_to_variations([_variation(1, "Неон")], {"videos": [_entry(1, "Неон", start=11.0)]}, {"from": 10.0, "to": 20.0}, TL)


def test_attach_refuses_a_video_missing_from_the_batch(monkeypatch) -> None:
    sb = _sb(monkeypatch)
    with pytest.raises(sb.StoryboardError, match="видео 3"):
        sb.attach_to_variations([_variation(1, "Неон")], {"videos": [_entry(3, "Неон")]}, {"from": 10.0, "to": 20.0}, TL)


def test_attach_refuses_a_plan_for_other_cuts(monkeypatch) -> None:
    """Склейки на таймлайне поменялись, а новая раскадровка ещё не пришла:
    старые склейки не должны молча уйти в рендер."""
    sb = _sb(monkeypatch)
    with pytest.raises(sb.StoryboardError, match="Склейки"):
        sb.attach_to_variations([_variation(1, "Неон")], {"videos": [_entry(1, "Неон")]},
                                {"from": 10.0, "to": 20.0}, {"cuts": [14.0]})
    with pytest.raises(sb.StoryboardError, match="Склейки"):
        sb.attach_to_variations([_variation(1, "Неон")], {"videos": [_entry(1, "Неон")]},
                                {"from": 10.0, "to": 20.0}, None)


# ── production ────────────────────────────────────────────────────────────────

def test_footage_plan_reaches_the_orchestrator(monkeypatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))
    job = _job()
    variation = job["renderJob"]["variations"][0]
    plan = {"version": 1, "clip_start_abs": 0.0, "clip_end_abs": 5.0, "switch_points_abs": [], "clips": []}
    variation["background"]["footagePlan"] = plan
    payload = backend._request_payload(job=job, variation=variation, index=1, total=2, master_id=None)
    assert payload["footage_plan"] is plan
    assert payload["rotation_theme"] == "visual" and payload["rotation_tags_group"] == "neon"


def test_storyboard_pick_sends_the_exact_slot(monkeypatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))
    sent: dict[str, Any] = {}

    def post(url: str, *, json: dict[str, Any]) -> _Response:
        sent.update(url=url, body=json)
        return _Response({"videos": []})

    backend._http.post = post
    backend.storyboard_pick(group_name="Неон", clip_start_abs=1.0, clip_end_abs=9.0,
                            switch_points_abs=[4.0], videos=[{"seed_key": "a", "pins": {}}])
    assert sent["url"].endswith("/storyboard/pick")
    assert sent["body"]["rotation_theme"] == "visual" and sent["body"]["rotation_tags_group"] == "neon"


def test_storyboard_refusal_is_passed_through(monkeypatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _config(module))
    backend._http.post = lambda url, *, json: _Response({"detail": "storyboard pick: No footage asset can cover interval"}, 422)
    with pytest.raises(module.ProductionBackendError, match="cover interval"):
        backend.storyboard_cuts(audio_s3_url="s3://a/t.mp3", clip_start_sec=0.0, clip_end_sec=5.0, user_drop_t=None)


# ── ручки (mock) ──────────────────────────────────────────────────────────────

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


def test_storyboard_endpoints_mock_flow(client) -> None:
    tc, main = client
    vibe = main.store.VIBES[0]["name"]
    r = tc.post("/api/wizard/storyboard/cuts", json={"clipFrom": "00:10", "clipTo": "00:25", "dropTime": "00:15:000"})
    assert r.status_code == 200
    cuts = r.json()["cuts"]["auto"]
    assert 15.0 in cuts

    r = tc.post("/api/wizard/storyboard/pick", json={"clipFrom": "00:10", "clipTo": "00:25", "cuts": cuts,
                                                     "videos": [{"index": 1, "group": vibe, "seedKey": "s1"}]})
    assert r.status_code == 200
    video = r.json()["videos"][0]
    assert len(video["clips"]) == len(cuts) + 1

    r = tc.post("/api/wizard/storyboard/alternatives", json={
        "clipFrom": "00:10", "clipTo": "00:25", "cuts": cuts, "group": vibe, "shot": 0, "seedKey": "s1",
        "exclude": [c["fileName"] for c in video["clips"]], "limit": 5})
    alts = [c["fileName"] for c in r.json()["candidates"]]
    assert len(alts) == 5 and not set(alts) & {c["fileName"] for c in video["clips"]}


def test_drop_outside_the_fragment_is_an_explicit_error(client) -> None:
    tc, _main = client
    r = tc.post("/api/wizard/storyboard/cuts", json={"clipFrom": "00:10", "clipTo": "00:25", "dropTime": "00:40:000"})
    assert r.status_code == 422 and "Дроп" in r.json()["detail"]


def test_storyboard_needs_a_clip_window(client) -> None:
    tc, _main = client
    r = tc.post("/api/wizard/storyboard/cuts", json={"clipFrom": "", "clipTo": ""})
    assert r.status_code == 422
