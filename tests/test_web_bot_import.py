"""«Докрутить на сайте»: ролики бота → визард на монтажном столе (web_app/backend/app/bot_import.py).

Главная проверка — круг: импорт → stageData визарда → render_job → запрос в оркестратор
даёт ровно те же поля, с которыми рендерил бот (вайб, склейки, клипы, хук, переход,
стиль, субтитры). Плюс обратные карты реестра ↔ id бота и явные отказы.
"""
from __future__ import annotations

import dataclasses
import importlib
import sys
from typing import Any

import pytest

from tests.test_web_production_backend import _backend, _config, _module

LYRICS = "раз два три\nчетыре пять"
VIBE = "Неон"


def _bot_import(monkeypatch):
    _module(monkeypatch)  # sys.path + env мок-режима
    for name in ("app.bot_import", "app.render_job", "app.montage", "app.storyboard"):
        sys.modules.pop(name, None)
    return importlib.import_module("app.bot_import")


def _catalog(bi):
    return bi.ImportCatalog(
        subtitle_modes={"Brat": "brat_5th", "Impulse": "impulse_2nd"},
        footage={
            VIBE: {"rotationTheme": "visual", "rotationTagsGroup": "neon", "renderPreset": "vertical", "plane": "vibes"},
            "Нью-Йорк": {"rotationTheme": "collection", "rotationTagsGroup": "ny", "renderPreset": "wide", "plane": "cine16x9"},
        },
        photo={"Портрет": {"rotationTheme": "photo", "rotationTagsGroup": "portrait"}},
    )


def _plan(start: float, end: float, cuts: list[float], names: list[str]) -> dict[str, Any]:
    bounds = [start, *cuts, end]
    return {
        "version": 1, "clip_start_abs": start, "clip_end_abs": end, "switch_points_abs": list(cuts),
        "clips": [
            {"file_name": n, "fit_mode": "cover", "framing": None, "in_point": bounds[i], "out_point": bounds[i + 1],
             "start_time": bounds[i] - 2.0, "source_offset_sec": 2.0}
            for i, n in enumerate(names)
        ],
    }


CUTS = [12.4, 14.9, 17.3]


def _request(**over) -> dict[str, Any]:
    req = {
        "audio_s3_url": "s3://raw-audio/raw/t.mp3", "target_fragment": LYRICS, "subtitles_mode": "brat_5th",
        "user_clip_start_sec": 10.1234, "user_clip_end_sec": 20.9876, "hook_enabled": True, "user_drop_t": 14.9,
        "f2_shape": "rhomb", "effect_transition": "minimax", "effect_extra": "xerox", "effect_extra_full": True,
        "subtitle_color_hex": "F6F5FD", "accent_color_hex": "#e38fb5", "rotation_theme": "visual",
        "rotation_tags_group": "neon", "bg_mode": "footage", "render_preset": "vertical",
    }
    req.update(over)
    return req


def _state(job_id: str, names: list[str], **req_over) -> dict[str, Any]:
    return {
        "job_id": job_id, "status": "SUCCEEDED", "request": _request(**req_over),
        # окно слов (рабочее окно выравнивателя) шире окна плана, прибитого к фразам
        "window": {"clip_start_abs": 10.1234, "clip_end_abs": 20.9876},
        "footage_plan": _plan(10.5, 20.5, CUTS, names),
        "footage_plan_meta": {"bg_mode": "footage", "exact_slot": True},
        "switch_points_abs": list(CUTS),
        "words": [{"text": "раз", "t_start": 10.2, "t_end": 10.5}],
        "asr": {"available": True, "mode": "local_ctc", "alignment_backend": "local_ctc", "reference_text": LYRICS},
    }


def _two_versions() -> list[dict[str, Any]]:
    return [_state("job-a", ["a1", "a2", "a3", "a4"]), _state("job-b", ["b1", "b2", "b3", "b4"])]


# ── обратные карты ────────────────────────────────────────────────────────────

def test_reverse_maps_round_trip_registry_and_cover_bot_ids(monkeypatch) -> None:
    bi = _bot_import(monkeypatch)
    em = importlib.import_module("app.effect_map")
    for label, manifest_id in em.HOOK_MAP.items():
        assert em.HOOK_MAP[bi.HOOK_LABEL_BY_ID[manifest_id]] == manifest_id
    for label, manifest_id in em.STYLE_MAP.items():
        assert em.STYLE_MAP[bi.STYLE_LABEL_BY_ID[manifest_id]] == manifest_id
    for manifest_id, label in bi.GLUE_LABEL_BY_ID.items():
        assert em.GLUE_MAP[label] == manifest_id
    # всё, что предлагает публичный бот (state_store: effect_hook/transition/extra)
    assert {"hook_light", "shutter_effect", "flash_slow_shutter"} <= set(bi.HOOK_LABEL_BY_ID)
    assert {"snap_wipe", "minimax", "invert_flash", "extract_flash", "flash_on_cuts"} <= set(bi.GLUE_LABEL_BY_ID)
    assert {"xerox", "analog_glitch", "neon_extract", "old_camera"} <= set(bi.STYLE_LABEL_BY_ID)
    # тряска слоя есть только в боте — переносится пометкой, а не подменой
    assert "layer_shake" not in bi.GLUE_LABEL_BY_ID
    # фигуры, движения, приёмы «Мысли» — все id бота, и обратно через прямые карты сайта
    assert set(bi.OBJECT_LABEL_BY_SHAPE) == {"rhomb", "square", "star1", "star2", "elipse"}
    for shape, label in bi.OBJECT_LABEL_BY_SHAPE.items():
        assert em.OBJECT_SCRIPT[label].endswith(f"_{shape}.jsx")
    assert set(bi.MOTION_LABEL_BY_DEVICE) == {"swipe", "tap", "pinch", "holdfinger", "head"}
    for device, label in bi.MOTION_LABEL_BY_DEVICE.items():
        assert em.MOTION_SCRIPT[label].endswith(f"_{device}.jsx")
    assert {em.THOUGHT_DEVICE[label] for label in bi.THOUGHT_LABEL_BY_DEVICE.values()} == set(em.THOUGHT_DEVICE.values())


# ── импорт ────────────────────────────────────────────────────────────────────

def test_two_versions_of_one_vibe_open_with_the_same_cuts_clips_and_settings(monkeypatch) -> None:
    bi = _bot_import(monkeypatch)
    imp = bi.build_wizard_import(_two_versions(), _catalog(bi))

    # отрывок = окно слов, округлённое НАРУЖУ до сотых (поля визарда мм:сс:сс)
    assert imp["timing"] == {"from": "00:10:12", "to": "00:20:99"}
    assert imp["window"] == {"start": 10.12, "end": 20.99}
    assert imp["timeline"] == {"pace": "auto", "cuts": CUTS}
    assert imp["dropTime"] == "00:14:90"
    assert imp["lyrics"] == LYRICS
    assert imp["background"]["footage"] == [VIBE] and imp["background"]["footageType"] == "vertical"
    assert imp["allocation"]["background"] == {f"footage:{VIBE}": 2}
    assert imp["allocation"]["subtitles"] == {"Brat": 2}
    assert imp["allocation"]["variants"] == {"v-bot-1": 2}
    assert imp["subtitles"]["color"] == "#f6f5fd"
    assert imp["subtitles"]["textByStyle"] == {"Brat": {"accentColor": "#e38fb5"}}
    variant = imp["fxVariants"][0]
    assert variant["kind"] == "object" and variant["config"]["object"] == "Ромб"
    assert variant["config"]["effectGlue"] == bi.GLUE_LABEL_BY_ID["minimax"]
    assert variant["config"]["effectStyles"] == [bi.STYLE_LABEL_BY_ID["xerox"]]
    # план: те же клипы, края дотянуты до отрывка, кадры в исходнике не сдвинуты
    plan = imp["storyboard"][0]["plan"]
    assert imp["storyboard"][0]["index"] == 1 and imp["storyboard"][1]["index"] == 2
    assert [c["file_name"] for c in plan["clips"]] == ["a1", "a2", "a3", "a4"]
    assert (plan["clip_start_abs"], plan["clip_end_abs"]) == (10.12, 20.99)
    assert plan["clips"][0]["in_point"] == 10.12 and plan["clips"][0]["start_time"] == 8.5
    assert abs(plan["clips"][0]["source_offset_sec"] - 1.62) < 1e-9
    assert plan["clips"][-1]["out_point"] == 20.99
    assert plan["clips"][1] == _two_versions()[0]["footage_plan"]["clips"][1]


def test_round_trip_renders_the_bot_request(monkeypatch) -> None:
    """импорт → stageData визарда → render_job → запрос в оркестратор: поля бота на месте."""
    bi = _bot_import(monkeypatch)
    pb = _module(monkeypatch)
    render_job = importlib.import_module("app.render_job")
    states = _two_versions()
    imp = bi.build_wizard_import(states, _catalog(bi))

    variant = imp["fxVariants"][0]
    stage_data = {
        "track": {"s3Key": "s3://raw-audio/raw/t.mp3", "durationS": 200},
        "lyrics": imp["lyrics"], "fragment": imp["lyrics"],
        "timing": imp["timing"],
        "background": imp["background"],
        "hooks": {"dropTime": imp["dropTime"], "configs": {}},
        "fxVariants": [variant],
        "allocation": {k: v for k, v in imp["allocation"].items()},
        "subtitles": imp["subtitles"],
        "timeline": {**imp["timeline"], "edited": True},
        "storyboard": {"key": "k", "videos": [{"index": e["index"], "group": e["group"], "plan": e["plan"]} for e in imp["storyboard"]]},
        "montage": {"videos": {}},
        "final": {"idempotencyKey": "idem"},
    }
    rj = render_job.build_render_job("batch", "project", "user", stage_data, 2)

    config = dataclasses.replace(
        _config(pb, stage1_backend="local_ctc"),
        subtitle_modes={"Brat": "brat_5th"},
        selector_by_mode={"footage": {VIBE: {"rotationTheme": "visual", "rotationTagsGroup": "neon", "renderPreset": "vertical"}}},
    )
    backend = _backend(pb, config)
    job = {"id": "web-job", "projectId": "p", "stageData": stage_data, "renderJob": rj}
    for i, state in enumerate(states):
        payload = backend._request_payload(job=job, variation=rj["variations"][i], index=i, total=2, master_id=None)
        bot = state["request"]
        for key in ("subtitles_mode", "f2_shape", "effect_transition", "effect_extra", "effect_extra_full",
                    "rotation_theme", "rotation_tags_group", "render_preset", "accent_color_hex", "target_fragment"):
            assert payload.get(key) == bot[key], key
        assert payload["subtitle_color_hex"].lower() == "#" + bot["subtitle_color_hex"].lower()
        assert payload["user_drop_t"] == pytest.approx(bot["user_drop_t"])
        # отрывок охватывает окно слов бота: выравниватель примет его Stage 1 без пересчёта
        assert payload["user_clip_start_sec"] <= 10.1234 and payload["user_clip_end_sec"] >= 20.9876
        # те же склейки и те же клипы на них
        plan = payload["footage_plan"]
        assert plan["switch_points_abs"] == CUTS
        assert [c["file_name"] for c in plan["clips"]] == [c["file_name"] for c in state["footage_plan"]["clips"]]
        assert "frame_id" not in payload


def test_frame_rides_on_the_montage_and_bot_only_transition_is_a_visible_note(monkeypatch) -> None:
    bi = _bot_import(monkeypatch)
    states = [_state("job-a", ["a1", "a2", "a3", "a4"], frame_id="rounded", effect_transition="layer_shake")]
    imp = bi.build_wizard_import(states, _catalog(bi))
    assert imp["frames"] == {0: "rounded"}
    assert imp["fxVariants"][0]["config"]["effectGlue"] == "Без склейки"
    assert any("layer_shake" in n for n in imp["notes"])


def test_settings_snapshot_fills_in_when_the_job_request_expired(monkeypatch) -> None:
    bi = _bot_import(monkeypatch)
    states = _two_versions()
    for s in states:
        s["request"] = None
        s["status"] = None
    snapshot = {
        "subtitlesMode": "brat_5th", "visualTransition": "minimax", "visualStyle": "xerox",
        "hookEnabled": True, "hookCategory": "effect", "effectHook": "hook_light",
        "vibeSelectedIds": ["visual:neon"], "hookDropT": 14.9, "frameId": "none",
    }
    imp = bi.build_wizard_import(states, _catalog(bi), snapshot=snapshot)
    variant = imp["fxVariants"][0]
    assert variant["kind"] == "effects" and variant["config"]["effectHook"] == "Молния"
    assert variant["config"]["effectStyleFull"] is True
    assert imp["frames"] == {}


@pytest.mark.parametrize("mutate, message", [
    (lambda s: s[1]["footage_plan"].update(switch_points_abs=[12.0, 15.0, 17.3]) or s[1].update(switch_points_abs=[12.0, 15.0, 17.3]), "Склейки"),
    (lambda s: s[1].update(window={"clip_start_abs": 30.0, "clip_end_abs": 40.0}), "разные отрывки"),
    (lambda s: [x["request"].update(rotation_tags_group="unknown") for x in s], "нет в каталоге"),
    (lambda s: [x["request"].update(subtitles_mode="legacy_blocks") for x in s], "Стиля субтитров"),
    (lambda s: s[1]["request"].update(f2_shape="star1"), "Хуки"),
    (lambda s: [x.update(status="FAILED") for x in s], "Ни один"),
])
def test_what_cannot_be_opened_exactly_is_an_explicit_error(monkeypatch, mutate, message) -> None:
    bi = _bot_import(monkeypatch)
    states = _two_versions()
    mutate(states)
    with pytest.raises(bi.BotImportError, match=message):
        bi.build_wizard_import(states, _catalog(bi))


def test_collections_and_photo_carry_cuts_but_no_storyboard(monkeypatch) -> None:
    bi = _bot_import(monkeypatch)
    wide = _state("job-a", ["a1", "a2", "a3", "a4"], rotation_theme="collection", rotation_tags_group="ny", render_preset="wide")
    imp = bi.build_wizard_import([wide], _catalog(bi))
    assert imp["storyboard"] == [] and imp["timeline"]["cuts"] == CUTS
    assert imp["background"]["footageType"] == "cine16x9"
    assert imp["background"]["footageFormats"] == {"Нью-Йорк": "16:9"}
    # 16:9 хука не получает: переход идёт через склейку фона
    assert imp["allocation"]["variants"] == {"v-bot-1": 0}
    assert imp["background"]["glue"]
    assert any("Коллекции" in n for n in imp["notes"])

    photo = _state("job-p", ["p1", "p2", "p3", "p4"], bg_mode="photo", rotation_theme="photo", rotation_tags_group="portrait")
    imp = bi.build_wizard_import([photo], _catalog(bi))
    assert imp["background"]["photo"] == ["Портрет"] and imp["storyboard"] == []
    assert imp["background"]["photoEffects"] is True


def test_failed_version_is_skipped_with_a_note(monkeypatch) -> None:
    bi = _bot_import(monkeypatch)
    states = _two_versions()
    states[0]["status"] = "FAILED"
    imp = bi.build_wizard_import(states, _catalog(bi))
    assert imp["allocation"]["total"] == 1
    assert any("не собрался" in n for n in imp["notes"])
