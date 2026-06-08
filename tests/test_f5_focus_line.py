# -*- coding: utf-8 -*-
"""F5 post-drop focus-line resolution + prompt targeting.

Verifies the fix: Stage1 must interact with the lyric line that lands AFTER the
drop (resolved from ASR word-timings), not the clip start / first line.
"""
from __future__ import annotations

from mlcore.hooks.f5_cognition.orchestrator_hook import _post_drop_focus_line
from mlcore.hooks.f5_cognition.models import F5Device, F5Request
from mlcore.hooks.f5_cognition.prompts import build_user_prompt


def _w(text, t0, t1):
    return {"text": text, "t_start": t0, "t_end": t1}


def test_focus_line_picks_phrase_after_drop():
    words = [
        _w("intro", 0.0, 0.4),
        _w("words", 0.4, 0.8),
        _w("before", 0.8, 1.2),
        # drop at 2.0 — phrase starts here
        _w("после", 2.0, 2.3),
        _w("дропа", 2.3, 2.6),
        _w("строка", 2.6, 3.0),
        # big pause → phrase ends
        _w("следующая", 4.0, 4.4),
    ]
    line = _post_drop_focus_line(words, drop_abs_sec=2.0)
    assert line == "после дропа строка"


def test_focus_line_breaks_on_pause_gap():
    words = [
        _w("a", 2.0, 2.2),
        _w("b", 2.2, 2.4),
        _w("c", 3.5, 3.7),  # gap 1.1s > 0.45 → break before c
    ]
    assert _post_drop_focus_line(words, drop_abs_sec=1.9) == "a b"


def test_focus_line_respects_max_words():
    words = [_w(str(i), 2.0 + i * 0.1, 2.0 + i * 0.1 + 0.08) for i in range(20)]
    line = _post_drop_focus_line(words, drop_abs_sec=2.0, max_words=5)
    assert len(line.split()) == 5


def test_focus_line_none_when_no_words_after_drop():
    words = [_w("only", 0.0, 0.5), _w("before", 0.5, 1.0)]
    assert _post_drop_focus_line(words, drop_abs_sec=5.0) is None


def test_focus_line_none_without_timings_or_drop():
    assert _post_drop_focus_line(None, drop_abs_sec=2.0) is None
    assert _post_drop_focus_line([_w("x", 2.0, 2.5)], drop_abs_sec=None) is None


def test_focus_line_ignores_malformed_words():
    words = [
        {"text": "ok", "t_start": 2.0, "t_end": 2.4},
        {"text": "bad"},  # missing timings → skipped
        {"text": "", "t_start": 2.5, "t_end": 2.9},  # empty text → skipped
    ]
    assert _post_drop_focus_line(words, drop_abs_sec=1.9) == "ok"


def _req(focus_line):
    return F5Request(
        track_path="/x.mp3",
        lyrics="первая строка\nвторая строка\nтретья строка",
        focal_start_ms=0,
        device=F5Device.PUNCHLINE,
        focus_line=focus_line,
    )


def test_prompt_targets_focus_line_when_present():
    prompt = build_user_prompt(_req("после дропа строка"))
    assert "ЦЕЛЕВАЯ СТРОКА" in prompt
    assert "после дропа строка" in prompt
    assert "СРАЗУ ПОСЛЕ дропа" in prompt


def test_prompt_falls_back_to_first_line_without_focus():
    prompt = build_user_prompt(_req(None))
    # No post-drop target → first lyric line is the fallback target.
    assert "первая строка" in prompt
    assert "ЦЕЛЕВАЯ СТРОКА" not in prompt
