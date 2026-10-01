"""Монтажный стол визарда → render_job → запрос в оркестратор.

Стол правит ролик целиком (хук, переход на каждой склейке, стили по окнам кадров, стиль
субтитров). Правка обязана дойти до рендера ровно такой, какой её видел человек, а
расхождение (устаревшая правка, хук на фото, разные переходы на своём видео) — быть явной
ошибкой, а не тихо пропасть.
"""
from __future__ import annotations

import importlib
from copy import deepcopy
from typing import Any

import pytest

from tests.test_web_production_backend import _backend, _config, _module

VARIANT = {"id": "v-light", "kind": "effects", "config": {
    "effectHook": "Молния", "effectGlue": "Щелчок", "effectStyles": ["Глитч"], "effectStyle": "Глитч"}}
CUTS = [12.0, 15.0, 18.0, 21.0]  # отрывок 10..25 -> 5 кадров


def _stage(*, photo: bool = False) -> dict[str, Any]:
    background = {"footage:Неон": 2}
    total = 2
    if photo:
        background["photo:Крупный план"] = 1
        total = 3
    return {
        "track": {"id": "t1", "s3Key": "s3://raw-audio/raw/track.mp3", "filename": "t.mp3"},
        "lyrics": "текст", "fragment": "текст",
        "timing": {"from": "00:10", "to": "00:25"},
        "background": {"mode": "footage", "footage": ["Неон"], "photo": ["Крупный план"] if photo else [], "uploads": []},
        "hooks": {"dropTime": "00:15:000", "configs": {}},
        "fxVariants": [deepcopy(VARIANT)],
        "subtitles": {"color": "#ffffff", "pool": ["Impulse", "Brat"], "textByStyle": {"Brat": {"size": "m"}}},
        "allocation": {"total": total, "background": background, "subtitles": {"Impulse": 2},
                       "hooks": {}, "styles": {}, "variants": {"v-light": 2}},
        "timeline": {"cuts": list(CUTS), "pace": "auto", "edited": False},
        "final": {"accentColor": "#8b6fe6"},
    }


def _edit(sig: str, **over: Any) -> dict[str, Any]:
    entry = {"sig": sig, "kind": "effects", "config": deepcopy(VARIANT["config"]),
             "transitions": {}, "styles": [], "sub": "Impulse"}
    entry.update(over)
    return entry


SIG = "footage:Неон|Impulse|v-light"


@pytest.fixture
def rj(monkeypatch: pytest.MonkeyPatch):
    _module(monkeypatch)
    return importlib.import_module("app.render_job")


def test_untouched_videos_keep_the_variant_path(rj) -> None:
    job = rj.build_render_job("b1", "p1", "u1", _stage(), 2)
    hook = job["variations"][0]["hook"]
    assert "cutTransitions" not in hook and "extraRanges" not in hook
    assert hook["resolved"]["transition"] == "snap_wipe" and hook["resolved"]["extra"] == "analog_glitch"


def test_transitions_follow_the_table_default_and_overrides(rj) -> None:
    stage = _stage()
    stage["montage"] = {"videos": {"0": _edit(SIG, transitions={"1": "Минимакс", "2": "Без склейки"})}}
    hook = rj.build_render_job("b1", "p1", "u1", stage, 2)["variations"][0]["hook"]
    # склейка по умолчанию — склейка хука ролика; «Без склейки» — перехода нет
    assert hook["cutTransitions"] == [
        {"tAbs": 12.0, "transition": "snap_wipe"},
        {"tAbs": 15.0, "transition": "minimax"},
        {"tAbs": 21.0, "transition": "snap_wipe"},
    ]
    assert hook["cutsAbs"] == CUTS
    assert hook["resolved"]["transition"] is None and hook["resolved"]["extra"] is None
    assert hook["resolved"]["hook"] == "hook_light"


def test_style_ranges_are_frame_windows_in_track_seconds(rj) -> None:
    stage = _stage()
    stage["montage"] = {"videos": {"1": _edit(SIG, styles=[
        {"uid": 1, "style": "Глитч", "lane": 0, "a": 0, "b": 2},
        {"uid": 2, "style": "Ч/Б", "lane": 1, "a": 1, "b": 5},
    ])}}
    hook = rj.build_render_job("b1", "p1", "u1", stage, 2)["variations"][1]["hook"]
    assert hook["extraRanges"] == [
        {"extra": "analog_glitch", "startAbs": 10.0, "endAbs": 15.0},
        {"extra": "blackwhite", "startAbs": 12.0, "endAbs": 25.0},
    ]


def test_table_subtitle_style_takes_its_own_text_settings(rj) -> None:
    stage = _stage()
    stage["montage"] = {"videos": {"0": _edit(SIG, sub="Brat")}}
    sub = rj.build_render_job("b1", "p1", "u1", stage, 2)["variations"][0]["subtitle"]
    assert sub["style"] == "Brat" and sub["text"] == {"size": "m"}


def test_stale_edit_is_explicit(rj) -> None:
    stage = _stage()
    stage["montage"] = {"videos": {"0": _edit("footage:Неон|Brat|v-light")}}
    with pytest.raises(ValueError, match="устарели"):
        rj.build_render_job("b1", "p1", "u1", stage, 2)
    stage["montage"] = {"videos": {"5": _edit(SIG)}}
    with pytest.raises(ValueError, match="видео 6 больше нет"):
        rj.build_render_job("b1", "p1", "u1", stage, 2)


def test_hook_on_a_photo_video_is_refused(rj) -> None:
    stage = _stage(photo=True)
    stage["allocation"]["subtitles"] = {"Impulse": 3}
    stage["montage"] = {"videos": {"2": _edit("photo:Крупный план|Impulse|")}}
    with pytest.raises(ValueError, match="только на вертикальное видео"):
        rj.build_render_job("b1", "p1", "u1", stage, 3)
    # без хука (переходы и стили) — фото принимает правки стола
    stage["montage"] = {"videos": {"2": _edit("photo:Крупный план|Impulse|", kind="none", config={"effectGlue": "Инверт"})}}
    hook = rj.build_render_job("b1", "p1", "u1", stage, 3)["variations"][2]["hook"]
    assert hook["family"] is None and [c["transition"] for c in hook["cutTransitions"]] == ["invert_flash"] * 4


def test_unknown_labels_and_out_of_frame_styles_are_explicit(rj) -> None:
    stage = _stage()
    stage["montage"] = {"videos": {"0": _edit(SIG, transitions={"0": "Телепорт"})}}
    with pytest.raises(ValueError, match="неизвестный переход"):
        rj.build_render_job("b1", "p1", "u1", stage, 2)
    stage["montage"] = {"videos": {"0": _edit(SIG, styles=[{"uid": 1, "style": "Глитч", "lane": 0, "a": 3, "b": 9}])}}
    with pytest.raises(ValueError, match="выходит за кадры"):
        rj.build_render_job("b1", "p1", "u1", stage, 2)


def test_payload_carries_table_edits_and_pins_the_auto_cuts(monkeypatch) -> None:
    module = _module(monkeypatch)
    rj = importlib.import_module("app.render_job")
    stage = _stage()
    stage["montage"] = {"videos": {"0": _edit(SIG, transitions={"1": "Минимакс"}, styles=[
        {"uid": 1, "style": "Ксерокс", "lane": 0, "a": 0, "b": 2}])}}
    job = rj.build_render_job("b1", "p1", "u1", stage, 2)
    backend = _backend(module, _config(module))
    web_job = {"id": "web-job", "projectId": "p1", "stageData": stage, "renderJob": job}
    edited = backend._request_payload(job=web_job, variation=job["variations"][0], index=1, total=2, master_id=None)
    plain = backend._request_payload(job=web_job, variation=job["variations"][1], index=2, total=2, master_id=None)
    assert "effect_transition" not in edited and "effect_extra" not in edited
    assert edited["effect_cut_transitions"][1] == {"t_abs": 15.0, "transition": "minimax"}
    assert edited["effect_extra_ranges"] == [{"extra": "xerox", "start_abs": 10.0, "end_abs": 15.0}]
    # «авто»-темп рецепт не шлёт, но переходы по склейкам требуют закрепить именно их
    assert edited["pinned_cuts"]["switch_points_abs"] == CUTS
    assert plain["effect_transition"] == "snap_wipe" and "effect_cut_transitions" not in plain
    assert "pinned_cuts" not in plain


def test_outdated_orchestrator_is_refused_before_enqueue(monkeypatch) -> None:
    module = _module(monkeypatch)
    rj = importlib.import_module("app.render_job")
    stage = _stage()
    stage["montage"] = {"videos": {"0": _edit(SIG)}}
    job = rj.build_render_job("b1", "p1", "u1", stage, 2)
    backend = _backend(module, _config(module))
    web_job = {"id": "web-job", "projectId": "p1", "stageData": stage, "renderJob": job,
               "videos": [{"id": "v1", "status": "PENDING"}, {"id": "v2", "status": "PENDING"}],
               "asrReuseJobId": None}
    with pytest.raises(module.ProductionBackendError, match="montage table edits"):
        backend.enqueue_job(web_job)
    assert backend._http.posts == []


def test_slider_color_goes_to_the_renderer_as_exact_hex(monkeypatch) -> None:
    module = _module(monkeypatch)
    rj = importlib.import_module("app.render_job")
    stage = _stage()
    stage["background"]["color"] = "#8b6fe6"
    stage["allocation"]["total"] = 3
    stage["allocation"]["colorFont"] = "Impulse"
    job = rj.build_render_job("b1", "p1", "u1", stage, 3)
    backend = _backend(module, _config(module))
    web_job = {"id": "web-job", "projectId": "p1", "stageData": stage, "renderJob": job}
    color = job["variations"][2]
    assert color["background"]["mode"] == "color"
    payload = backend._request_payload(job=web_job, variation=color, index=3, total=3, master_id=None)
    assert payload["bg_mode"] == "solid" and payload["bg_solid_color"] == "#8b6fe6"
    color["background"]["color"] = "фиолетовый"
    with pytest.raises(module.ProductionBackendError, match="Цвет фона не распознан"):
        backend._request_payload(job=web_job, variation=color, index=3, total=3, master_id=None)


def test_table_hooks_join_the_upload_checks(rj) -> None:
    # прогрев, поставленный на столе, тоже проверяется на владение файлом и получает
    # метаданные с сервера — иначе прод отказал бы «загрузите файл заново»
    stage = _stage()
    warm = {"warmupKind": "audio", "soundUrl": "s3://assets/warm.mp3", "effectGlue": "Щелчок"}
    stage["montage"] = {"videos": {"0": _edit(SIG, kind="warmup", config=warm), "1": _edit(SIG, kind="none", config={})}}
    configs = rj.selected_hook_configs(stage)
    assert ("warmup", stage["montage"]["videos"]["0"]["config"]) in configs
    assert all(kind != "none" for kind, _ in configs)


def test_frame_from_the_table_reaches_the_render(monkeypatch) -> None:
    module = _module(monkeypatch)
    rj = importlib.import_module("app.render_job")
    stage = _stage()
    stage["montage"] = {"videos": {"0": _edit(SIG, frame="letterbox")}}
    job = rj.build_render_job("b1", "p1", "u1", stage, 2)
    assert job["variations"][0]["frame"] == "letterbox"
    backend = _backend(module, _config(module))
    web_job = {"id": "web-job", "projectId": "p1", "stageData": stage, "renderJob": job}
    edited = backend._request_payload(job=web_job, variation=job["variations"][0], index=1, total=2, master_id=None)
    plain = backend._request_payload(job=web_job, variation=job["variations"][1], index=2, total=2, master_id=None)
    assert edited["frame_id"] == "letterbox" and "frame_id" not in plain


def test_unknown_or_wide_frame_is_explicit(rj) -> None:
    stage = _stage()
    stage["montage"] = {"videos": {"0": _edit(SIG, frame="neon")}}
    with pytest.raises(ValueError, match="Неизвестная рамка"):
        rj.build_render_job("b1", "p1", "u1", stage, 2)
    stage = _stage()
    stage["background"]["footageFormats"] = {"Неон": "16:9"}
    stage["allocation"]["variants"] = {"v-light": 0}
    stage["montage"] = {"videos": {"0": _edit("footage:Неон|Impulse|", kind="none", config={}, frame="rounded")}}
    with pytest.raises(ValueError, match="только на вертикальное видео"):
        rj.build_render_job("b1", "p1", "u1", stage, 2)
