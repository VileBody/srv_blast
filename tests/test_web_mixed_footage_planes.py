"""Батч, смешивающий вайбы и фильмы: каждый ролик идёт со СВОЕЙ подборкой.

Выпадающий список типа футажей — только какая подборка открыта сейчас. Раньше
render_job слал один footageType на весь батч, и рендер брал запись каталога по
одной подписи — одноимённая запись другой подборки молча подменяла футаж.
"""
from __future__ import annotations

import dataclasses
import importlib
from typing import Any

import pytest

from tests.test_web_production_backend import _backend, _config, _job, _module
from web_app.backend.app.render_job import build_render_job


def _stage(background: dict[str, Any]) -> dict[str, Any]:
    groups = background["footage"]
    return {
        "background": {"mode": "footage", **background},
        "subtitles": {"pool": ["Impulse"]},
        "hooks": {},
        "allocation": {
            "total": len(groups),
            "background": {f"footage:{g}": 1 for g in groups},
            "subtitles": {"Impulse": len(groups)},
        },
        "track": {"s3Key": "s3://audio/track.wav", "durationS": 20},
        "timing": {"from": "00:00", "to": "00:10"},
        "lyrics": "line",
        "final": {},
    }


def _backgrounds(stage: dict[str, Any]) -> list[dict[str, Any]]:
    return [v["background"] for v in build_render_job("batch", "project", "user", stage, len(stage["background"]["footage"]))["variations"]]


def test_mixed_batch_sends_each_video_with_its_own_plane() -> None:
    # список типа остался на «Фильмах», но «Неон» выбран из вайбов
    stage = _stage({
        "footage": ["Неон", "Титаник"],
        "footageType": "films",
        "footageFormats": {"Неон": "9:16", "Титаник": "9:16"},
        "footagePlanes": {"Неон": "vibes", "Титаник": "films"},
    })
    neon, titanic = _backgrounds(stage)
    assert (neon["footageType"], neon["footagePlane"]) == ("vertical", "vibes")
    assert (titanic["footageType"], titanic["footagePlane"]) == ("films", "films")


def test_recorded_collection_plane_sets_wide_format_without_footage_formats() -> None:
    stage = _stage({
        "footage": ["Нью-Йорк", "Неон"],
        "footageType": "vertical",
        "footagePlanes": {"Нью-Йорк": "cine16x9", "Неон": "vibes"},
    })
    ny, neon = _backgrounds(stage)
    assert (ny["footageType"], ny["sourceFormat"]) == ("cine16x9", "16:9")
    assert (neon["footageType"], neon["sourceFormat"]) == ("vertical", "9:16")


def test_legacy_draft_without_planes_keeps_batch_footage_type() -> None:
    stage = _stage({"footage": ["Неон", "Титаник"], "footageType": "films"})
    backgrounds = _backgrounds(stage)
    assert [b["footageType"] for b in backgrounds] == ["films", "films"]
    assert [b["footagePlane"] for b in backgrounds] == [None, None]


def test_group_picked_before_planes_existed_keeps_batch_type() -> None:
    stage = _stage({
        "footage": ["Старый", "Титаник"],
        "footageType": "vertical",
        "footagePlanes": {"Титаник": "films"},
    })
    old, titanic = _backgrounds(stage)
    assert (old["footageType"], old["footagePlane"]) == ("vertical", None)
    assert (titanic["footageType"], titanic["footagePlane"]) == ("films", "films")


def test_unknown_plane_is_an_explicit_error() -> None:
    stage = _stage({"footage": ["Неон"], "footageType": "vertical", "footagePlanes": {"Неон": "people"}})
    with pytest.raises(ValueError, match="неизвестная подборка"):
        _backgrounds(stage)


def _two_plane_config(module: Any) -> Any:
    vibe = {"rotationTheme": "visual", "rotationTagsGroup": "neon", "renderPreset": "vertical"}
    film = {"rotationTheme": "collection", "rotationTagsGroup": "films__Neon", "renderPreset": "vertical"}
    return dataclasses.replace(
        _config(module),
        footage_catalog=(
            {"id": "visual:neon", "plane": "vibes", "name": "Неон", "previewUrl": "s3://a/n.mp4", "score": 1.0, "selector": vibe},
            {"id": "collection:films__Neon", "plane": "films", "name": "Неон", "previewUrl": "s3://a/f.mp4", "score": 1.0, "selector": film},
        ),
        # карта по имени: последняя запись затёрла вайб фильмом — так и было до фикса
        selector_by_mode={"footage": {"Неон": film}, "photo": {}},
    )


def test_same_label_in_two_planes_renders_each_from_its_own_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _two_plane_config(module))
    job = _job()
    vibe_video, film_video = job["renderJob"]["variations"]
    vibe_video["background"] = {"mode": "footage", "groups": ["Неон"], "footagePlane": "vibes"}
    film_video["background"] = {"mode": "footage", "groups": ["Неон"], "footagePlane": "films"}

    vibe_payload = backend._request_payload(job=job, variation=vibe_video, index=1, total=2, master_id=None)
    film_payload = backend._request_payload(job=job, variation=film_video, index=2, total=2, master_id=None)
    assert (vibe_payload["rotation_theme"], vibe_payload["rotation_tags_group"]) == ("visual", "neon")
    assert (film_payload["rotation_theme"], film_payload["rotation_tags_group"]) == ("collection", "films__Neon")


def test_render_job_planes_reach_orchestrator_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    vibe = {"rotationTheme": "visual", "rotationTagsGroup": "neon", "renderPreset": "vertical"}
    wide = {"rotationTheme": "collection", "rotationTagsGroup": "cine16x9__NY", "renderPreset": "wide"}
    config = dataclasses.replace(
        _config(module),
        footage_catalog=(
            {"id": "visual:neon", "plane": "vibes", "name": "Неон", "previewUrl": "s3://a/n.mp4", "score": 1.0, "selector": vibe},
            {"id": "collection:cine16x9__NY", "plane": "cine16x9", "name": "Нью-Йорк", "previewUrl": "s3://a/w.mp4", "score": 1.0, "selector": wide},
        ),
        selector_by_mode={"footage": {"Неон": vibe, "Нью-Йорк": wide}, "photo": {}},
    )
    backend = _backend(module, config)
    render_job = importlib.import_module("app.render_job")
    stage = _stage({
        "footage": ["Неон", "Нью-Йорк"],
        "footageType": "cine16x9",
        "footagePlanes": {"Неон": "vibes", "Нью-Йорк": "cine16x9"},
    })
    built = render_job.build_render_job("batch", "project", "user", stage, 2)
    job = {**_job(), "renderJob": {**_job()["renderJob"], "variations": built["variations"]}}
    payloads = [
        backend._request_payload(job=job, variation=v, index=i + 1, total=2, master_id=None)
        for i, v in enumerate(built["variations"])
    ]
    assert [p["render_preset"] for p in payloads] == ["vertical", "wide"]
    assert [p["rotation_tags_group"] for p in payloads] == ["neon", "cine16x9__NY"]


def test_legacy_variation_without_plane_keeps_name_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _two_plane_config(module))
    job = _job()
    variation = job["renderJob"]["variations"][0]
    payload = backend._request_payload(job=job, variation=variation, index=1, total=1, master_id=None)
    assert payload["rotation_tags_group"] == "films__Neon"


def test_plane_without_matching_catalog_entry_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    backend = _backend(module, _two_plane_config(module))
    job = _job()
    variation = job["renderJob"]["variations"][0]
    variation["background"] = {"mode": "footage", "groups": ["Неон"], "footagePlane": "cine16x9"}
    with pytest.raises(module.ProductionBackendError, match="not found in plane 'cine16x9'"):
        backend._request_payload(job=job, variation=variation, index=1, total=1, master_id=None)
