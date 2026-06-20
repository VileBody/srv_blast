# -*- coding: utf-8 -*-
"""F5 Stage1: an out-of-range expected_duration_ms estimate from the model must
not crash all of F5. Regression for job 08a98492… (missing_word, sub-1500ms
estimate tripped VoiceSpec ge=1500 → render had no voice)."""
from __future__ import annotations

import json

from mlcore.hooks.f5_cognition.stage1_text import (
    _parse_voice_spec,
    TARGET_DURATION_MIN_MS,
    TARGET_DURATION_MAX_MS,
)


def _raw(edm):
    return json.dumps({
        "tts_text": "Кто же эта таинственная незнакомка?",
        "voice_persona": "Женский, молодой, томный",
        "voice_emotion": "whisper",
        "voice_pacing": "slow",
        "expected_duration_ms": edm,
        "rationale": "r",
    }, ensure_ascii=False)


def test_sub_floor_estimate_does_not_crash():
    # 1200 < VoiceSpec ge=1500 → previously crashed; now pre-clamped then
    # target-clamped up to TARGET_DURATION_MIN_MS.
    spec = _parse_voice_spec(_raw(1200))
    assert spec.expected_duration_ms == TARGET_DURATION_MIN_MS


def test_over_ceiling_estimate_does_not_crash():
    spec = _parse_voice_spec(_raw(5000))
    assert spec.expected_duration_ms == TARGET_DURATION_MAX_MS


def test_in_range_estimate_kept():
    spec = _parse_voice_spec(_raw(3000))
    assert spec.expected_duration_ms == 3000
