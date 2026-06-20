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


def test_word_timings_filters_post_window_and_clamps_end():
    words = [
        {"text": "in", "t_start": 2.0, "t_end": 2.5},
        {"text": "edge", "t_start": 4.8, "t_end": 5.4},   # straddles clip_end 5.0
        {"text": "after", "t_start": 5.1, "t_end": 5.6},  # past clip_end → dropped
    ]
    wt = word_timings_from_transcript(words, clip_start=2.0, clip_end=5.0)
    assert [w["word"] for w in wt] == ["in", "edge"]
    # edge end clamped to window (5.0-2.0=3.0)
    assert abs(wt[1]["end"] - 3.0) < 1e-9


def test_word_timings_accepts_transcriptword_objects():
    wt = word_timings_from_transcript([_Word("привет", 0.0, 0.5)], clip_start=0.0)
    assert wt == [{"word": "привет", "start": 0.0, "end": 0.5, "focus": False}]


def test_trim_phrase_to_spoken():
    from app.jsx_subtitles_builder import trim_phrase_to_spoken

    # the real case: 7.4s speech cut to 4.0s → keep round(7*4000/7400)=4 words
    p = "Я абсолютно не расслаблен, под полным контролем."
    assert trim_phrase_to_spoken(p, audio_ms=4000, tts_ms=7400) == "Я абсолютно не расслаблен,"
    # no cut (tts <= audio) → unchanged
    assert trim_phrase_to_spoken(p, audio_ms=4000, tts_ms=4000) == p
    assert trim_phrase_to_spoken(p, audio_ms=4000, tts_ms=0) == p
    # never trims below 1 word
    assert trim_phrase_to_spoken("один два", audio_ms=10, tts_ms=99999) == "один"
    assert trim_phrase_to_spoken("", audio_ms=1, tts_ms=9) == ""


def test_word_timings_strips_edge_punctuation():
    words = [
        {"text": "Дэнсил,", "t_start": 0.0, "t_end": 0.4},
        {"text": "город...", "t_start": 0.4, "t_end": 0.8},
        {"text": "«Burberry»", "t_start": 0.8, "t_end": 1.2},
        {"text": "don't", "t_start": 1.2, "t_end": 1.6},   # intra-word apostrophe kept
        {"text": "—", "t_start": 1.6, "t_end": 1.7},        # punctuation-only → dropped
    ]
    wt = word_timings_from_transcript(words, clip_start=0.0)
    assert [w["word"] for w in wt] == ["Дэнсил", "город", "Burberry", "don't"]


def test_splice_voice_phrase_replaces_window_words():
    from app.jsx_subtitles_builder import splice_voice_phrase

    wt = [
        {"word": "a", "start": 0.0, "end": 0.5, "focus": False},
        {"word": "b", "start": 1.0, "end": 1.5, "focus": False},   # inside window → dropped
        {"word": "c", "start": 1.6, "end": 2.0, "focus": False},   # inside window → dropped
        {"word": "d", "start": 3.0, "end": 3.5, "focus": False},
    ]
    out = splice_voice_phrase(wt, window_start=0.9, window_end=2.1, phrase="мысль моя")
    words = [w["word"] for w in out]
    # clip words b,c removed; voice words inserted; a,d kept
    assert "b" not in words and "c" not in words
    assert "a" in words and "d" in words
    assert "мысль" in words and "моя" in words
    # voice words sit inside the window and are sorted
    voice = [w for w in out if w["word"] in ("мысль", "моя")]
    assert abs(voice[0]["start"] - 0.9) < 1e-6
    assert abs(voice[-1]["end"] - 2.1) < 1e-6
    assert out == sorted(out, key=lambda w: w["start"])
    # voice words are marked so brat keeps them in their own container
    assert all(w.get("voice") for w in voice)
    assert not any(w.get("voice") for w in out if w["word"] in ("a", "d"))


def test_splice_voice_phrase_noop_on_empty():
    from app.jsx_subtitles_builder import splice_voice_phrase

    wt = [{"word": "a", "start": 0.0, "end": 0.5, "focus": False}]
    assert splice_voice_phrase(wt, window_start=1.0, window_end=2.0, phrase="  ") == wt
    assert splice_voice_phrase(wt, window_start=2.0, window_end=1.0, phrase="x") == wt


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


def test_hex_to_rgb01():
    from app.jsx_subtitles_builder import hex_to_rgb01

    assert hex_to_rgb01("#FFFFFF") == [1.0, 1.0, 1.0]
    assert hex_to_rgb01("000000") == [0.0, 0.0, 0.0]
    r = hex_to_rgb01("#FF0000")
    assert r[0] == 1.0 and r[1] == 0.0 and r[2] == 0.0
    assert hex_to_rgb01("nope") is None
    assert hex_to_rgb01("") is None


def test_overlay_injects_custom_fill_color():
    wt = word_timings_from_transcript([{"text": "йо", "t_start": 0.0, "t_end": 0.4}])
    js = build_jsx_subtitles_overlay(
        mode=SUBTITLES_MODE_TRENDY_5TH, word_timings=wt, fill_hex="#FF0000",
    )
    assert "$.global.__BLAST_FILL = [1.0, 0.0, 0.0]" in js
    # script reads it
    assert "injectedFill" in js
    # no fill ASSIGNMENT injected when not requested (the reader helper stays)
    js2 = build_jsx_subtitles_overlay(mode=SUBTITLES_MODE_TRENDY_5TH, word_timings=wt)
    assert "$.global.__BLAST_FILL = [" not in js2


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


def test_schema_accepts_5th_modes():
    from services.orchestrator.schemas import SendAudioS3Request

    for m in ("trendy_5th", "brat_5th"):
        req = SendAudioS3Request(
            audio_s3_url="https://example.com/a.mp3",
            mode="with_gemini",
            lyrics_text="x",
            target_fragment="x",
            subtitles_mode=m,
        )
        assert req.subtitles_mode == m


def test_template_has_jsx_subtitles_token():
    from pathlib import Path

    tpl = Path("templates/project_template.j2").read_text(encoding="utf-8")
    assert "{{ jsx_subtitles_js }}" in tpl


def test_overlay_rejects_bad_mode_and_empty():
    with pytest.raises(ValueError, match="not a 5th JSX mode"):
        build_jsx_subtitles_overlay(mode="impulse_2nd", word_timings=[{"word": "x", "start": 0, "end": 1, "focus": False}])
    with pytest.raises(ValueError, match="empty word_timings"):
        build_jsx_subtitles_overlay(mode=SUBTITLES_MODE_TRENDY_5TH, word_timings=[])
