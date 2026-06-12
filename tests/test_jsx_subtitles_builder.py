# -*- coding: utf-8 -*-
"""5th-template JSX subtitles: word-timings emitter + overlay builder."""
from __future__ import annotations

import json

import pytest

from core.subtitles_mode import (
    SUBTITLES_MODE_BRAT_5TH,
    SUBTITLES_MODE_TRENDY_5TH,
    SUBTITLES_MODE_JSX_5TH,
)
from app.jsx_subtitles_builder import (
    build_jsx_subtitles_overlay,
    word_timings_from_transcript,
)


class _Word:
    """TranscriptWord-like duck."""
    def __init__(self, text, t_start, t_end, focus=False):
        self.text, self.t_start, self.t_end, self.focus = text, t_start, t_end, focus


def test_modes_registered():
    assert SUBTITLES_MODE_TRENDY_5TH in SUBTITLES_MODE_JSX_5TH
    assert SUBTITLES_MODE_BRAT_5TH in SUBTITLES_MODE_JSX_5TH


def test_word_timings_shape_and_comp_relative():
    words = [
        {"text": "если", "t_start": 12.5, "t_end": 12.8},
        {"text": "хочешь", "t_start": 12.8, "t_end": 13.2, "focus": True},
    ]
    wt = word_timings_from_transcript(words, clip_start=12.0)
    assert wt == [
        {"word": "если", "start": 0.5, "end": 0.8, "focus": False},
        {"word": "хочешь", "start": 0.8, "end": 1.2, "focus": True},
    ]


def test_word_timings_drops_pre_window_and_clamps_straddler():
    words = [
        {"text": "before", "t_start": 1.0, "t_end": 1.9},   # fully before clip 2.0 → dropped
        {"text": "edge", "t_start": 1.8, "t_end": 2.4},      # straddles → start clamped to 0
        {"text": "in", "t_start": 3.0, "t_end": 3.5},
    ]
    wt = word_timings_from_transcript(words, clip_start=2.0)
    assert [w["word"] for w in wt] == ["edge", "in"]
    assert wt[0]["start"] == 0.0
    assert abs(wt[0]["end"] - 0.4) < 1e-9


def test_word_timings_accepts_transcriptword_objects():
    wt = word_timings_from_transcript([_Word("привет", 0.0, 0.5)], clip_start=0.0)
    assert wt == [{"word": "привет", "start": 0.0, "end": 0.5, "focus": False}]


def test_overlay_trendy_inlines_json_and_disables_dialog():
    wt = word_timings_from_transcript([{"text": "йо", "t_start": 0.0, "t_end": 0.4}])
    js = build_jsx_subtitles_overlay(mode=SUBTITLES_MODE_TRENDY_5TH, word_timings=wt)
    assert "$.global.__BLAST_SUBS_JSON = " in js
    assert '"word_timings"' in js
    assert '$.global.__BLAST_TARGET_COMP = "Comp 1"' in js
    # headless: no dialog/alert
    assert "INTERACTIVE:     false" in js
    assert "DEBUG:           false" in js
    # trendy doesn't use bpm
    assert "__BLAST_BPM" not in js
    # script body present
    assert "addSapphire" in js
    # injected JSON parses
    start = js.index("__BLAST_SUBS_JSON = ") + len("__BLAST_SUBS_JSON = ")
    end = js.index(";\n", start)
    assert json.loads(js[start:end])["word_timings"][0]["word"] == "йо"


def test_overlay_brat_injects_bpm():
    wt = word_timings_from_transcript([{"text": "бам", "t_start": 0.0, "t_end": 0.4}])
    js = build_jsx_subtitles_overlay(
        mode=SUBTITLES_MODE_BRAT_5TH, word_timings=wt, bpm=128.0,
    )
    assert "$.global.__BLAST_BPM = 128.0" in js
    assert "addBlinker" in js
    assert "INTERACTIVE:     false" in js


def test_project_builder_consumes_subtitles_jsx_block():
    from app.project_builder import _build_jsx_subtitles_js

    cfg = {"subtitles_jsx": {
        "mode": SUBTITLES_MODE_BRAT_5TH,
        "word_timings": [{"word": "йо", "start": 0.0, "end": 0.4, "focus": False}],
        "bpm": 124.0,
    }}
    js = _build_jsx_subtitles_js(cfg)
    assert "$.global.__BLAST_BPM = 124.0" in js
    assert "addBlinker" in js
    # absent block → no-op
    assert _build_jsx_subtitles_js({}) == ""


def test_template_has_jsx_subtitles_token():
    from pathlib import Path

    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert "{{ jsx_subtitles_js }}" in tpl


def test_overlay_rejects_bad_mode_and_empty():
    with pytest.raises(ValueError, match="not a 5th JSX mode"):
        build_jsx_subtitles_overlay(mode="impulse_2nd", word_timings=[{"word": "x", "start": 0, "end": 1, "focus": False}])
    with pytest.raises(ValueError, match="empty word_timings"):
        build_jsx_subtitles_overlay(mode=SUBTITLES_MODE_TRENDY_5TH, word_timings=[])
