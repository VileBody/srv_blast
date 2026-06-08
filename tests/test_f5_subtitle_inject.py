# -*- coding: utf-8 -*-
"""F5 subtitle inject: split into sequential chunks + strip stale reveal."""
from __future__ import annotations

from mlcore.hooks.f5_cognition.inject import (
    F5_AUDIO_ENVELOPE,
    _split_tts_text,
    _strip_time_animated,
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


def test_split_tts_text():
    assert _split_tts_text("a b c d e", max_words=3) == ["a b c", "d e"]
    assert _split_tts_text("one two", max_words=3) == ["one two"]
    assert _split_tts_text("") == []
    assert _split_tts_text("   ") == []


def test_strip_time_animated_removes_keyframed_entries():
    d = {
        "position": {"match_name": "ADBE Position", "value": [1, 2]},
        "reveal": {"match_name": "ADBE Text Percent Start", "keyframes": [{"t": 0, "v": 0}]},
    }
    out = _strip_time_animated(d)
    assert "position" in out
    assert "reveal" not in out
    assert _strip_time_animated(None) == {}


def test_subtitle_split_into_sequential_chunks():
    layers = inject_subtitle_layer([_template_text_layer()], _f5(), focal_start_ms=0)
    # template had no overlap-clear issues; result = chunk layers only (template
    # was the style source, consumed). 5 words / 3 = 2 chunks.
    chunks = [L for L in layers if str(L.get("name", "")).startswith("f5_hook_subtitle_")]
    assert len(chunks) == 2
    # sequential, covering [0, 3.0]
    assert chunks[0]["in_point"] == 0.0
    assert abs(chunks[0]["out_point"] - 1.5) < 1e-6
    assert abs(chunks[1]["in_point"] - 1.5) < 1e-6
    assert abs(chunks[1]["out_point"] - 3.0) < 1e-6
    assert chunks[0]["text"] == "первое второе третье"
    assert chunks[1]["text"] == "четвёртое пятое"
    # z-index strictly increasing above template
    assert chunks[0]["z_index"] == 1001
    assert chunks[1]["z_index"] == 1002


def test_subtitle_chunks_strip_reveal_and_disable_animator():
    layers = inject_subtitle_layer([_template_text_layer()], _f5(), focal_start_ms=0)
    chunks = [L for L in layers if str(L.get("name", "")).startswith("f5_hook_subtitle_")]
    for L in chunks:
        assert "reveal" not in (L.get("props") or {})  # stale keyframes gone
        assert "position" in (L.get("props") or {})     # static style kept
        td = L["text_data"]
        assert td.get("no_text_animator") is True
        assert "text_animator" not in td
        # char styles rebuilt to chunk length
        assert len(td["char_styles_ungrouped"]) == len(L["text"])


def test_subtitle_single_chunk_when_short():
    layers = inject_subtitle_layer([_template_text_layer()], _f5(text="бум", dur_ms=2500), focal_start_ms=500)
    chunks = [L for L in layers if str(L.get("name", "")).startswith("f5_hook_subtitle_")]
    assert len(chunks) == 1
    assert chunks[0]["in_point"] == 0.5
    assert abs(chunks[0]["out_point"] - 3.0) < 1e-6


def test_subtitle_no_template_is_noop():
    # No text layers to clone style from → unchanged input.
    assert inject_subtitle_layer([], _f5(), focal_start_ms=0) == []


def test_f5_audio_envelope_has_volume_ramp():
    assert F5_AUDIO_ENVELOPE["ramp_start_pct"] == 25.0
    assert F5_AUDIO_ENVELOPE["ramp_end_pct"] == 100.0


def test_template_audio_envelope_expr_supports_ramp():
    from pathlib import Path

    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert "ramp_start_pct" in tpl
    assert "ramp_end_pct" in tpl
    assert "var rampOn=" in tpl
