# -*- coding: utf-8 -*-
"""F5 TTS robustness: a blocked/empty Gemini response (HTTP 200 but
content=None) must be RETRIED inside synthesize_voice, not abort F5 entirely.
Regression for job 01e2db5c… (missing_word voice silently skipped).
"""
from __future__ import annotations

import types

import pytest

import mlcore.hooks.f5_cognition.stage2_audio as s2
from mlcore.hooks.f5_cognition.errors import F5GeminiTimeout
from mlcore.hooks.f5_cognition.models import VoiceSpec


def _spec():
    return VoiceSpec(
        tts_text="Думаешь, мы останемся на",
        voice_persona="female, young, warm",
        voice_emotion="whisper",
        voice_pacing="slow",
        expected_duration_ms=3000,
        rationale="r",
    )


def test_blocked_then_success_retries(monkeypatch):
    calls = {"n": 0}

    def fake_call(prompt, *, spec, model):
        calls["n"] += 1
        if calls["n"] == 1:
            # First call: blocked (content=None) → retryable error.
            raise F5GeminiTimeout("empty content (finish_reason=SAFETY)")
        return b"FAKE_WAV"

    monkeypatch.setattr(s2, "_call_gemini_tts", fake_call)
    monkeypatch.setattr(s2, "_measure_duration_ms", lambda b: 3000)

    audio, dur = s2.synthesize_voice(_spec())
    assert audio == b"FAKE_WAV"
    assert dur == 3000
    assert calls["n"] == 2  # retried once after the block


def test_too_long_retries_then_accepts_shorter(monkeypatch):
    # First take is over 4s (would be cut mid-phrase) → retry asking to speak
    # faster; second take fits the window → returned.
    durations = iter([7400, 3600])
    hints = []

    def fake_call(prompt, *, spec, model):
        hints.append("слишком длинной" in prompt)
        return b"WAV"

    monkeypatch.setattr(s2, "_call_gemini_tts", fake_call)
    monkeypatch.setattr(s2, "_measure_duration_ms", lambda b: next(durations))

    audio, dur = s2.synthesize_voice(_spec())
    assert dur == 3600                 # in-window take returned, not the 7400 one
    assert hints[1] is True            # 2nd attempt got the "too long" hint


def test_all_too_long_returns_shortest(monkeypatch):
    # Every take overshoots 4s → return the SHORTEST (least to cut), don't raise.
    durations = iter([8000, 5200, 6100])

    monkeypatch.setattr(s2, "_call_gemini_tts", lambda p, *, spec, model: b"WAV")
    monkeypatch.setattr(s2, "_measure_duration_ms", lambda b: next(durations))

    audio, dur = s2.synthesize_voice(_spec())
    assert dur == 5200                 # shortest over-long take


def test_all_blocked_raises_gemini_timeout(monkeypatch):
    def fake_call(prompt, *, spec, model):
        raise F5GeminiTimeout("empty content (finish_reason=OTHER)")

    monkeypatch.setattr(s2, "_call_gemini_tts", fake_call)
    monkeypatch.setattr(s2, "_measure_duration_ms", lambda b: 3000)

    with pytest.raises(F5GeminiTimeout, match="finish_reason"):
        s2.synthesize_voice(_spec())


def test_parse_empty_content_raises_with_finish_reason(monkeypatch):
    # Build a fake resp: candidate with content=None, finish_reason set.
    cand = types.SimpleNamespace(content=None, finish_reason="SAFETY")
    resp = types.SimpleNamespace(candidates=[cand], prompt_feedback=None)

    class _FakeModels:
        def generate_content(self, **kw):
            return resp

    class _FakeClient:
        models = _FakeModels()

    monkeypatch.setattr(s2, "make_client", lambda: _FakeClient())

    with pytest.raises(F5GeminiTimeout, match="empty content.*SAFETY"):
        s2._call_gemini_tts("p", spec=_spec(), model="gemini-2.5-flash-preview-tts")
