# -*- coding: utf-8 -*-
"""F5 subtitle inject: clone the track subtitle type 1:1 + retime reveal."""
from __future__ import annotations

from mlcore.hooks.f5_cognition.inject import (
    F5_AUDIO_ENVELOPE,
    _retime_keyframes_inplace,
    inject_subtitle_layer,
)
from mlcore.hooks.f5_cognition.models import F5Device, F5Response


def _f5(text="первое второе третье четвёртое пятое", dur_ms=3000):
    return F5Response(
        audio_path="/x.wav",
        audio_duration_ms=dur_ms,
        tts_text=text,
        voice_persona="p",
        voice_emotion="hype",
        voice_pacing="normal",
        tts_duration_ms=dur_ms,
        chosen_device=F5Device.PUNCHLINE,
        rationale="r",
    )


def _template_text_layer():
    return {
        "name": "scene_001",
        "type": "text",
        "in_point": 1.0,
        "out_point": 2.5,
        "z_index": 1000,
        "text": "оригинал",
        "props": {
            "position": {"match_name": "ADBE Position", "value": [540, 1700]},
            # stale reveal driver with keyframes — must be stripped on clone
            "reveal": {"match_name": "ADBE Text Percent Start", "keyframes": [
                {"t": 1.0, "v": 0}, {"t": 1.2, "v": 100},
            ]},
        },
        "effects": {},
        "text_data": {
            "layer_meta": {"startTime": 1.0, "enabled": True},
            "text_animator": {"some": "cfg"},
            "char_styles_ungrouped": [{"i": 0, "font": "X", "fontSize": 90}],
        },
    }


def test_retime_keyframes_inplace_remaps_times():
    obj = {"reveal": {"keyframes": [{"t": 1.0, "v": 0}, {"t": 2.5, "v": 100}]}}
    _retime_keyframes_inplace(obj, src_in=1.0, src_out=2.5, dst_in=0.0, dst_out=3.0)
    kfs = obj["reveal"]["keyframes"]
    assert abs(kfs[0]["t"] - 0.0) < 1e-6   # 1.0 -> dst_in
    assert abs(kfs[1]["t"] - 3.0) < 1e-6   # 2.5 -> dst_out


def test_voice_subtitle_keeps_track_type_and_retimes_reveal():
    layers = inject_subtitle_layer([_template_text_layer()], _f5(), focal_start_ms=0)
    subs = [L for L in layers if str(L.get("name", "")).startswith("f5_hook_subtitle")]
    # ONE layer (same type as the track), not stripped chunks.
    assert len(subs) == 1
    L = subs[0]
    assert L["in_point"] == 0.0
    assert abs(L["out_point"] - 3.0) < 1e-6
    assert L["text"] == "первое второе третье четвёртое пятое"
    # Animator preserved (same type as track) — NOT disabled.
    td = L["text_data"]
    assert td.get("no_text_animator") is not True
    assert td.get("text_animator") == {"some": "cfg"}
    # Reveal keyframes retimed from template window [1.0,2.5] onto [0.0,3.0].
    kfs = L["props"]["reveal"]["keyframes"]
    assert abs(kfs[0]["t"] - 0.0) < 1e-6
    assert abs(kfs[1]["t"] - 0.4) < 1e-6   # (1.2-1.0)/1.5*3.0 = 0.4
    # char styles rebuilt to full text length
    assert len(td["char_styles_ungrouped"]) == len(L["text"])


def test_voice_subtitle_position_style_preserved():
    layers = inject_subtitle_layer([_template_text_layer()], _f5(), focal_start_ms=0)
    L = next(x for x in layers if str(x.get("name", "")).startswith("f5_hook_subtitle"))
    # static style (position) carried over unchanged — same look as the track.
    assert L["props"]["position"]["value"] == [540, 1700]


def test_voice_subtitle_short_phrase_one_layer():
    layers = inject_subtitle_layer([_template_text_layer()], _f5(text="бум", dur_ms=2500), focal_start_ms=500)
    subs = [L for L in layers if str(L.get("name", "")).startswith("f5_hook_subtitle")]
    assert len(subs) == 1
    assert subs[0]["in_point"] == 0.5
    assert abs(subs[0]["out_point"] - 3.0) < 1e-6


def test_subtitle_no_template_is_noop():
    # No text layers to clone style from → unchanged input.
    assert inject_subtitle_layer([], _f5(), focal_start_ms=0) == []


def test_f5_voice_envelope_has_no_ramp():
    # Voice plays at full volume; the TRACK is ducked instead.
    assert "ramp_start_pct" not in F5_AUDIO_ENVELOPE
    assert "ramp_end_pct" not in F5_AUDIO_ENVELOPE


def test_template_audio_envelope_expr_supports_track_duck():
    from pathlib import Path

    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert "duck_from_s" in tpl
    assert "duck_to_s" in tpl
    # Duck is applied via explicit keyframes (reliable in headless aerender),
    # not an expression.
    assert "audio duck keyframes" in tpl
    assert "setValueAtTime(duckFrom" in tpl


def _track_audio_layer():
    return {
        "name": "audio_track",
        "type": "footage",
        "in_point": 0.0,
        "out_point": 30.0,
        "z_index": 2,
        "text_data": {
            "layer_meta": {"audioEnabled": True, "comp_name_target": "Comp 1"},
            "source_footage": {"file_name": "track.mp3", "remote_url": "s3://b/track.mp3"},
            "audio_envelope": {"fade_in_s": 0.5, "fade_out_s": 0.5, "min_db": -48.0},
        },
    }


def test_inject_track_duck_sets_duck_fields_on_track():
    from mlcore.hooks.f5_cognition.inject import inject_track_duck

    out = inject_track_duck([_track_audio_layer()], duck_from_sec=0.0, duck_to_sec=4.0)
    env = out[0]["text_data"]["audio_envelope"]
    assert env["duck_from_s"] == 0.0
    assert env["duck_to_s"] == 4.0
    assert env["duck_from_pct"] == 25.0
    assert env["duck_to_pct"] == 100.0
    # existing fades preserved
    assert env["fade_in_s"] == 0.5


def test_inject_track_duck_skips_non_track_and_f5_layers():
    from mlcore.hooks.f5_cognition.inject import inject_track_duck

    f5_voice = {
        "name": "f5_hook_punchline", "type": "footage",
        "text_data": {"layer_meta": {"audioEnabled": True}, "audio_envelope": {}},
    }
    video = {
        "name": "clip1", "type": "footage",
        "text_data": {"layer_meta": {"audioEnabled": False}},
    }
    out = inject_track_duck([f5_voice, video], duck_from_sec=0.0, duck_to_sec=4.0)
    # neither got duck fields (f5 voice excluded, video has no audio)
    assert "duck_from_s" not in (out[0]["text_data"].get("audio_envelope") or {})
    assert "audio_envelope" not in out[1]["text_data"] or \
        "duck_from_s" not in out[1]["text_data"]["audio_envelope"]


def test_inject_track_duck_noop_on_bad_window():
    from mlcore.hooks.f5_cognition.inject import inject_track_duck

    layers = [_track_audio_layer()]
    out = inject_track_duck(layers, duck_from_sec=4.0, duck_to_sec=4.0)
    assert "duck_from_s" not in out[0]["text_data"]["audio_envelope"]


def test_apply_f5_ducks_track_with_drop():
    from mlcore.hooks.f5_cognition.inject import apply_f5

    track = _track_audio_layer()
    footage, _ = apply_f5(
        footage_layers=[track],
        text_layers=[_template_text_layer()],
        f5=_f5(),
        focal_start_ms=0,
        tts_remote_url="s3://b/voice.wav",
        drop_rel_sec=5.0,
    )
    # track ducked
    track_out = next(L for L in footage if L["name"] == "audio_track")
    env = track_out["text_data"]["audio_envelope"]
    assert env["duck_from_s"] == 0.0 and env["duck_to_s"] == 5.0
    # f5 voice layer added at full volume (no duck fields)
    voice = next(L for L in footage if str(L["name"]).startswith("f5_hook"))
    assert "duck_from_s" not in (voice["text_data"].get("audio_envelope") or {})


def test_f5_visual_combo_overlay_built_from_block():
    from app.project_builder import _build_f5_overlay_js

    cfg = {"f5": {"drop_rel_sec": 3.48, "combo_seed": 12345}}
    js = _build_f5_overlay_js(cfg)
    assert "DROP: F3 hook_light" in js
    assert "buildBolt" in js                       # rebuild_light.jsx body
    assert "__f2_groups" in js                     # post-drop random transitions
    assert "PRE-DROP shape transitions" not in js  # no shapes (like F1)


def test_f5_visual_combo_absent_without_drop():
    from app.project_builder import _build_f5_overlay_js

    assert _build_f5_overlay_js({}) == ""
    assert _build_f5_overlay_js({"f5": {"audio_url": "x"}}) == ""           # no drop
    assert _build_f5_overlay_js({"f5": {"drop_rel_sec": 3.0}}) == ""        # no seed
