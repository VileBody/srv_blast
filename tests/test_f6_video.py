# -*- coding: utf-8 -*-
"""F6 «Видео» build-side: окно/скейл, инъекция слоя, гашение субтитров, проводка."""
from __future__ import annotations

import pytest

from mlcore.hooks.f6_video.inject import (
    F6_LEAD_PAD_SEC,
    F6_TAIL_PAD_SEC,
    F6_VIDEO_Z_INDEX,
    clear_track_subtitles_under_video,
    cover_scale_percent,
    f6_video_window,
    inject_f6_video,
)
from mlcore.hooks.f6_video.overlay import build_overlay_jsx as f6_overlay


# ---------- окно ----------

def test_window_formula_matches_f1():
    in_sec, out_sec = f6_video_window(6.0)
    assert in_sec == F6_LEAD_PAD_SEC
    assert out_sec == 6.0 - F6_TAIL_PAD_SEC


def test_window_clamped_by_actual_duration():
    # Окно шире вырезки → out_point режется по факту, а не морозит последний кадр.
    _in, out_sec = f6_video_window(10.0, duration=3.0)
    assert out_sec == pytest.approx(3.5)


def test_window_ignores_duration_longer_than_the_gap():
    # Вырезка длиннее окна → AE подрежет её сам, окно остаётся до дропа.
    _in, out_sec = f6_video_window(6.0, duration=30.0)
    assert out_sec == pytest.approx(5.5)


def test_window_rejects_non_positive_duration():
    with pytest.raises(ValueError, match="duration must be > 0"):
        f6_video_window(6.0, duration=0.0)


# ---------- cover scale ----------

def test_cover_scale_landscape_source_into_vertical_comp():
    # 1920x1080 в 1080x1960: масштаб задаёт высота (1960/1080).
    s = cover_scale_percent(source_width=1920, source_height=1080,
                            comp_width=1080, comp_height=1960)
    assert s == pytest.approx(1960 / 1080 * 100)


def test_cover_scale_exact_fit_is_100_percent():
    s = cover_scale_percent(source_width=1080, source_height=1960,
                            comp_width=1080, comp_height=1960)
    assert s == pytest.approx(100.0)


def test_cover_scale_rejects_bad_source_size():
    with pytest.raises(ValueError, match="bad source size"):
        cover_scale_percent(source_width=0, source_height=1080,
                            comp_width=1080, comp_height=1960)


# ---------- инъекция слоя ----------

def _inject(footage_layers=None, **kw):
    params = dict(
        video_url="s3://bucket/hooks/interview.mp4",
        drop_time=6.0,
        source_width=1920,
        source_height=1080,
        comp_width=1080,
        comp_height=1960,
    )
    params.update(kw)
    return inject_f6_video(list(footage_layers or []), **params)


def test_inject_adds_a_remote_video_layer():
    layers = _inject()
    assert len(layers) == 1
    L = layers[0]
    assert L["type"] == "footage"
    assert L["name"] == "f6_hook_video"
    assert L["in_point"] == 0.5
    assert L["out_point"] == pytest.approx(5.5)
    src = L["text_data"]["source_footage"]
    assert src["remote_url"] == "s3://bucket/hooks/interview.mp4"
    assert src["file_name"] == "interview.mp4"
    assert src["file_path"] == ""


def test_inject_keeps_the_clip_audio_on():
    meta = _inject()[0]["text_data"]["layer_meta"]
    assert meta["audioEnabled"] is True
    assert meta["enabled"] is True
    assert meta["startTime"] == 0.5


def test_inject_sits_above_footage_and_below_subtitles():
    # В этом проекте меньший z = выше в стеке: текст=1, аудио=2, футаж=100+.
    assert 2 < F6_VIDEO_Z_INDEX < 100
    assert _inject()[0]["z_index"] == F6_VIDEO_Z_INDEX


def test_inject_bakes_cover_transform_without_expressions():
    props = _inject()[0]["props"]
    assert props["tf_anchor"]["value"] == [960.0, 540.0, 0.0]
    assert props["tf_position"]["value"] == [540.0, 980.0, 0.0]
    scale = props["tf_scale"]["value"]
    assert scale[0] == pytest.approx(1960 / 1080 * 100)
    assert scale[0] == scale[1]
    # Выражения в headless aerender ненадёжны — всё должно быть числами.
    assert all(p["expression"] is None for p in props.values())


def test_inject_does_not_mutate_input():
    src: list = []
    inject_f6_video(
        src, video_url="s3://b/v.mp4", drop_time=6.0,
        source_width=1920, source_height=1080, comp_width=1080, comp_height=1960,
    )
    assert src == []


def test_inject_rejects_non_positive_window():
    with pytest.raises(ValueError, match="non-positive window"):
        _inject(drop_time=0.8)


def test_inject_rejects_empty_url():
    with pytest.raises(ValueError, match="video_url is empty"):
        _inject(video_url="  ")


# ---------- трек-субтитры под видео ----------

def _text_layer(name, in_p, out_p):
    return {"name": name, "type": "text", "in_point": in_p, "out_point": out_p,
            "z_index": 1000, "text": name, "text_data": {}}


def test_track_subtitles_under_the_video_are_removed():
    layers = [
        _text_layer("under", 1.0, 2.0),       # внутри окна
        _text_layer("straddling", 5.0, 7.0),  # заезжает в окно
        _text_layer("after", 8.0, 9.0),       # после дропа — остаётся
    ]
    kept = clear_track_subtitles_under_video(layers, drop_time=6.0)
    assert [L["name"] for L in kept] == ["after"]


def test_subtitle_clearing_respects_the_clamped_window():
    # Вырезка 2с при дропе 10с → окно [0.5, 2.5], строка на 4с остаётся.
    layers = [_text_layer("inside", 1.0, 2.0), _text_layer("later", 4.0, 5.0)]
    kept = clear_track_subtitles_under_video(layers, drop_time=10.0, duration=2.0)
    assert [L["name"] for L in kept] == ["later"]


# ---------- визуал-комбо ----------

def test_overlay_is_the_f1_combo():
    js = f6_overlay(drop_time=6.0, seed=7)
    assert "PRE-DROP shape transitions" not in js
    assert "DROP: F3 hook_light" in js
    assert "var __f2_seed = 7" in js


def test_overlay_deterministic_for_same_seed():
    assert f6_overlay(drop_time=6.0, seed=42) == f6_overlay(drop_time=6.0, seed=42)


# ---------- проводка ----------

def _cfg(**kw):
    block = {
        "video_url": "s3://b/interview.mp4",
        "drop_time": 6.0,
        "seed": 99,
        "source_width": 1920,
        "source_height": 1080,
    }
    block.update(kw)
    return {"f6": block}


def test_project_builder_f6_dispatch():
    from app.project_builder import _apply_f6_if_present, _build_f6_overlay_js

    cfg = _cfg()
    js = _build_f6_overlay_js(cfg)
    assert "DROP: F3 hook_light" in js
    assert "var __f2_seed = 99" in js

    footage, text = _apply_f6_if_present(
        full_edit_config=cfg, footage_layers=[], text_layers=[],
        main_comp_name="Comp 1", comp_width=1080, comp_height=1960,
    )
    assert footage[-1]["name"] == "f6_hook_video"
    assert footage[-1]["text_data"]["source_footage"]["remote_url"] == "s3://b/interview.mp4"
    assert text == []


def test_project_builder_f6_ducks_the_track():
    from app.project_builder import _apply_f6_if_present
    from mlcore.hooks.f6_video.inject import F6_TRACK_DUCK_FROM_PCT

    track = {
        "name": "audio_track", "type": "footage", "in_point": 0.0, "out_point": 30.0,
        "z_index": 2,
        "text_data": {"layer_meta": {"audioEnabled": True, "comp_name_target": "Comp 1"},
                      "audio_envelope": {"fade_in_s": 0.5}},
    }
    footage, _text = _apply_f6_if_present(
        full_edit_config=_cfg(), footage_layers=[track], text_layers=[],
        main_comp_name="Comp 1", comp_width=1080, comp_height=1960,
    )
    duck = next(L for L in footage if L["name"] == "audio_track")["text_data"]["audio_envelope"]
    assert duck["duck_from_s"] == 0.5 and duck["duck_to_s"] == 6.0
    # под интервью трек глушится сильнее, чем под F5-голос
    assert duck["duck_from_pct"] == F6_TRACK_DUCK_FROM_PCT


def test_project_builder_f6_requires_source_size():
    from app.project_builder import _apply_f6_if_present

    cfg = _cfg()
    cfg["f6"].pop("source_width")
    with pytest.raises(RuntimeError, match="source size is missing"):
        _apply_f6_if_present(
            full_edit_config=cfg, footage_layers=[], text_layers=[],
            main_comp_name="Comp 1", comp_width=1080, comp_height=1960,
        )


def test_project_builder_no_f6_block_is_noop():
    from app.project_builder import _apply_f6_if_present, _build_f6_overlay_js

    assert _build_f6_overlay_js({}) == ""
    footage, text = _apply_f6_if_present(
        full_edit_config={}, footage_layers=[], text_layers=[],
        main_comp_name="Comp 1", comp_width=1080, comp_height=1960,
    )
    assert footage == [] and text == []


def test_template_has_f6_token():
    from pathlib import Path

    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert "{{ f6_overlay_js }}" in tpl
    assert "F6 «Видео» visual combo" in tpl


def test_render_plan_round_trips_the_f6_block():
    """Оверлей строится из visual ops, а не из исходного конфига — drop/seed
    обязаны пережить round-trip, иначе визуал молча пропадёт."""
    from app.render_plan import build_visual_ops

    ops = build_visual_ops(
        subtitles_mode="scenes_3rd", full_edit_config=_cfg(), f3_media=[],
    )
    op = next(o for o in ops if o.type == "hook.f6.video.v1")
    assert op.assets[0].role == "video"
    assert op.assets[0].path == "media/video/interview.mp4"
    assert op.params["seed"] == 99
    assert op.params["drop_time"] == 6.0


def test_media_manifest_downloads_the_user_video(tmp_path):
    """Ролик доезжает до ноды тем же media[]-транспортом, что футаж."""
    import json

    from services.orchestrator.render_manifest import collect_media_urls_from_render_payload

    layers = inject_f6_video(
        [], video_url="s3://bucket/hooks/interview.mp4", drop_time=6.0,
        source_width=1920, source_height=1080, comp_width=1080, comp_height=1960,
    )
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"footage_layers": layers}), encoding="utf-8")
    media = collect_media_urls_from_render_payload(payload, audio_url="")
    assert {"url": "s3://bucket/hooks/interview.mp4",
            "relpath": "media/video/interview.mp4"} in media


def test_jsx_subtitle_words_are_cleared_under_the_video():
    from app.jsx_subtitles_builder import clear_words_in_window

    words = [
        {"word": "под", "start": 1.0, "end": 1.4},
        {"word": "видео", "start": 2.0, "end": 2.4},
        {"word": "после", "start": 7.0, "end": 7.4},
    ]
    in_sec, out_sec = f6_video_window(6.0)
    kept = clear_words_in_window(words, window_start=in_sec, window_end=out_sec)
    assert [w["word"] for w in kept] == ["после"]


def test_schema_f6_requires_drop_and_size():
    from services.orchestrator.schemas import SendAudioS3Request

    base = dict(
        audio_s3_url="https://example.com/a.mp3",
        mode="with_gemini",
        lyrics_text="x",
        target_fragment="x",
        f6_video_url="https://example.com/interview.mp4",
    )
    with pytest.raises(ValueError, match="f6_video_url requires user_drop_t"):
        SendAudioS3Request(**base, f6_video_width=1920, f6_video_height=1080)
    with pytest.raises(ValueError, match="f6_video_width and f6_video_height"):
        SendAudioS3Request(**base, user_drop_t=6.0)


def test_every_f6_env_var_survives_the_in_process_env_bridge():
    """Все F6_* env обязаны быть в _LLM_ENV_KEYS.

    In-process оркестратор прокидывает только этот список и ВЫБРАСЫВАЕТ
    остальное: пропущенный ключ = f6_block молча None и ролик без прогрева.
    Ровно так в июне потерялись f3/f4-эффекты (журнал 2026-06-05).
    """
    import re

    from services.orchestrator.tasks import _LLM_ENV_KEYS

    with open("services/orchestrator/tasks.py", encoding="utf-8") as fh:
        src = fh.read()
    used = set(re.findall(r'env\["(F6_[A-Z_]+)"\]', src))
    assert used, "в tasks.py не нашлось ни одного F6_* env — проводка пропала"
    assert used <= set(_LLM_ENV_KEYS), used - set(_LLM_ENV_KEYS)


def test_orchestrator_reads_the_same_f6_env_names():
    """Имена env на записи (tasks) и на чтении (оркестратор) должны совпадать."""
    import re

    with open("services/orchestrator/tasks.py", encoding="utf-8") as fh:
        written = set(re.findall(r'env\["(F6_[A-Z_]+)"\]', fh.read()))
    with open("mlcore/gemini_orchestrator.py", encoding="utf-8") as fh:
        read = set(re.findall(r'os\.environ\.get\("(F6_[A-Z_]+)"', fh.read()))
    assert written == read, (written ^ read)


# ---- позиция в стеке AE ----

def _stack(z_values):
    """Список слоёв в том порядке, в каком его строит footage_comp: по
    возрастанию z (индекс 0 = самый верхний слой в AE)."""
    return [
        {"name": f"z{z}", "type": "footage", "in_point": 0.0, "out_point": 10.0,
         "z_index": z, "text_data": {}}
        for z in z_values
    ]


def test_video_lands_above_footage_and_below_the_subtitle_precomp():
    """Порядок массива = порядок стека AE (шаблон идёт с конца, layers.add кладёт
    наверх), и z_index дальше нигде не пересортировывается. Просто дописать слой
    в конец значило бы положить видео ПОД весь футаж — его было бы не видно."""
    # текст=1, аудио трека=2, аджастмент-грейд=10, футаж=100..102
    layers = _inject(footage_layers=_stack([1, 2, 10, 100, 101, 102]))
    names = [L["name"] for L in layers]
    i = names.index("f6_hook_video")

    assert names[:i] == ["z1", "z2"], "видео должно быть ниже субтитров и аудио"
    assert names[i + 1:] == ["z10", "z100", "z101", "z102"], (
        "видео должно быть ВЫШЕ грейд-аджастментов и всего футажа"
    )


def test_video_stays_above_footage_even_after_f1_f5_appended_their_audio():
    """F1/F5 дописывают свои аудио-слои в конец, ломая строгую сортировку
    массива. Для звука это безобидно, но вставка видео не должна на этом
    сломаться и уехать в конец списка."""
    layers = _inject(
        footage_layers=_stack([1, 2, 10, 100, 101]) + _stack([5])  # «аудио» F5 в конце
    )
    names = [L["name"] for L in layers]
    i = names.index("f6_hook_video")
    assert names.index("z100") > i
    assert names.index("z10") > i


def test_video_goes_first_when_there_is_nothing_above_it():
    layers = _inject(footage_layers=_stack([100, 101]))
    assert layers[0]["name"] == "f6_hook_video"
