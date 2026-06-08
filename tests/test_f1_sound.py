# -*- coding: utf-8 -*-
"""F1 «Звук» build-side: visual combo (no shapes) + audio inject + wiring."""
from __future__ import annotations

import pytest

from mlcore.hooks.f1_sound.overlay import build_overlay_jsx as f1_overlay
from mlcore.hooks.f1_sound.inject import (
    F1_LEAD_PAD_SEC,
    F1_TAIL_PAD_SEC,
    f1_audio_window,
    inject_f1_audio,
)
from mlcore.hooks.f2_object.overlay import build_overlay_jsx as f2_overlay


# ---------- visual combo (F2 with shape=None) ----------

def test_f2_overlay_shape_none_skips_predrop_shapes():
    js = f2_overlay(shape=None, drop_time=4.0, seed=1)
    # No shape script body (those carry `name: "<shape>"`), no pre-drop phase.
    assert "PRE-DROP shape transitions" not in js
    for shp in ("rhomb", "square", "star1", "star2", "elipse"):
        assert f'name: "{shp}"' not in js
    # But the drop hook_light + post-drop random remain.
    assert "DROP: F3 hook_light" in js
    assert "buildVspyshka" in js  # rebuild_light.jsx body
    assert "__f2_groups" in js


def test_f1_overlay_is_combo_without_shapes():
    js = f1_overlay(drop_time=4.0, seed=7)
    assert "PRE-DROP shape transitions" not in js
    assert "DROP: F3 hook_light" in js
    assert "buildBolt" in js
    assert "var __f2_seed = 7" in js


def test_f1_overlay_deterministic_for_same_seed():
    assert f1_overlay(drop_time=5.0, seed=42) == f1_overlay(drop_time=5.0, seed=42)


# ---------- audio inject ----------

def test_f1_audio_window_formula():
    in_sec, out_sec = f1_audio_window(4.0)
    assert in_sec == F1_LEAD_PAD_SEC
    assert out_sec == 4.0 - F1_TAIL_PAD_SEC


def test_inject_f1_audio_appends_remote_audio_layer():
    layers = inject_f1_audio(
        [], sound_url="s3://bucket/hooks/riser.mp3", drop_time=4.0,
    )
    assert len(layers) == 1
    L = layers[0]
    assert L["type"] == "footage"
    assert L["name"] == "f1_hook_sound"
    assert L["in_point"] == 0.5
    assert L["out_point"] == 3.5
    sf = L["text_data"]["source_footage"]
    assert sf["remote_url"] == "s3://bucket/hooks/riser.mp3"
    assert sf["file_name"] == "riser.mp3"
    assert L["text_data"]["layer_meta"]["audioEnabled"] is True


def test_inject_f1_audio_does_not_mutate_input():
    src = [{"name": "x"}]
    out = inject_f1_audio(src, sound_url="s3://b/s.wav", drop_time=4.0)
    assert len(src) == 1 and len(out) == 2


def test_inject_f1_audio_rejects_non_positive_window():
    # drop_time must exceed LEAD+TAIL (=1.0) for a positive window.
    with pytest.raises(ValueError, match="non-positive window"):
        inject_f1_audio([], sound_url="s3://b/s.wav", drop_time=0.9)


def test_inject_f1_audio_rejects_empty_url():
    with pytest.raises(ValueError, match="sound_url is empty"):
        inject_f1_audio([], sound_url="", drop_time=4.0)


# ---------- project_builder wiring ----------

def test_project_builder_f1_dispatch():
    from app.project_builder import _build_f1_overlay_js, _apply_f1_audio_if_present

    cfg = {"f1": {"sound_url": "s3://b/riser.mp3", "drop_time": 4.0, "seed": 99}}
    js = _build_f1_overlay_js(cfg)
    assert "DROP: F3 hook_light" in js
    assert "var __f2_seed = 99" in js

    layers = _apply_f1_audio_if_present(
        full_edit_config=cfg, footage_layers=[], main_comp_name="Comp 1",
    )
    assert layers[-1]["name"] == "f1_hook_sound"
    assert layers[-1]["text_data"]["source_footage"]["remote_url"] == "s3://b/riser.mp3"


def test_project_builder_no_f1_block_is_noop():
    from app.project_builder import _build_f1_overlay_js, _apply_f1_audio_if_present

    assert _build_f1_overlay_js({}) == ""
    assert _apply_f1_audio_if_present(full_edit_config={}, footage_layers=[], main_comp_name="Comp 1") == []


def test_template_has_f1_token():
    from pathlib import Path

    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert "{{ f1_overlay_js }}" in tpl
    assert "F1 «Звук» visual combo" in tpl


def test_schema_f1_requires_drop():
    from services.orchestrator.schemas import SendAudioS3Request

    with pytest.raises(ValueError, match="f1_sound_url requires user_drop_t"):
        SendAudioS3Request(
            audio_s3_url="https://example.com/a.mp3",
            mode="with_gemini",
            lyrics_text="x",
            target_fragment="x",
            f1_sound_url="https://example.com/riser.mp3",
            user_drop_t=None,
        )
    ok = SendAudioS3Request(
        audio_s3_url="https://example.com/a.mp3",
        mode="with_gemini",
        lyrics_text="x",
        target_fragment="x",
        f1_sound_url="https://example.com/riser.mp3",
        user_drop_t=4.5,
    )
    assert ok.f1_sound_url.endswith("riser.mp3")
