"""
Regression tests for STAGE2_TIMING_MODE=hook_aware (Phase A of hook feature).

Pure prompt-assembly tests — no LLM calls, no librosa runs on real audio.
Verifies that:
 - `hook_aware` is an accepted timing_mode for system & user prompt builders
 - `hook_analysis` kw threads through into the user prompt under a stable key
 - existing `hybrid` / `prompts` modes are not affected when the kw is omitted
 - bad modes are still rejected
"""

from __future__ import annotations

import json

import pytest

from mlcore.prompts.assemble import (
    build_stage2_timing_analysis_system_instruction,
    build_stage2_timing_analysis_user_prompt,
    build_stage2_timing_cuts_system_instruction,
    build_stage2_timing_cuts_user_prompt,
)


_STAGE1_STUB = {"audio": {"clip_start_abs": 0.0, "clip_end_abs": 22.0}}
_SUBS_STUB: dict = {}
_HOOK_FIXTURE = {
    "analysis_version": "v7",
    "bpm": 120.0,
    "bpm_raw": 120.0,
    "bpm_doubled": False,
    "beats": [0.5, 1.0, 1.5, 2.0],
    "downbeats": [0.5, 2.5],
    "onsets": [0.5, 0.7, 1.0, 1.3, 1.5],
    "drop_candidates": [
        {"t": 5.0, "confidence": 0.95, "score_raw": 6.1, "score_adj": 6.6,
         "snapped_to_beat": True, "source": "rms_jump+flux+low_band"},
    ],
    "spectral_peaks": [],
    "sections": [
        {"t_start": 0.0, "t_end": 5.0, "label": "build",
         "mean_density": 0.20, "peak_density": 0.30, "max_cuts_per_sec": 0.55},
        {"t_start": 5.0, "t_end": 8.0, "label": "drop",
         "mean_density": 0.90, "peak_density": 0.95, "max_cuts_per_sec": 1.70},
        {"t_start": 8.0, "t_end": 22.0, "label": "mid",
         "mean_density": 0.65, "peak_density": 0.80, "max_cuts_per_sec": 0.70},
    ],
}


def test_hook_aware_system_instruction_has_module():
    for builder in (
        build_stage2_timing_analysis_system_instruction,
        build_stage2_timing_cuts_system_instruction,
    ):
        text = builder(timing_mode="hook_aware")
        assert "HOOK_AWARE module" in text, f"{builder.__name__} missing HOOK_AWARE block"
        assert "HOOK_ANALYSIS_JSON" in text, f"{builder.__name__} should reference HOOK_ANALYSIS_JSON"


def test_hook_aware_analysis_user_prompt_embeds_hook_json():
    prompt = build_stage2_timing_analysis_user_prompt(
        stage1_json=_STAGE1_STUB,
        subtitles_json=_SUBS_STUB,
        bpm=120.0,
        fast_start_seconds=5.0,
        timing_mode="hook_aware",
        hook_analysis=_HOOK_FIXTURE,
    )
    assert "HOOK_ANALYSIS_JSON:" in prompt
    # the section labels and drop_t must reach the LLM verbatim
    assert "\"label\": \"drop\"" in prompt
    assert "\"label\": \"build\"" in prompt
    assert "5.0" in prompt  # drop_candidates[0].t
    # extracting the JSON block back must roundtrip
    raw = prompt.split("HOOK_ANALYSIS_JSON:\n", 1)[1].split("\n\n", 1)[0]
    assert json.loads(raw)["bpm"] == 120.0


def test_hook_aware_cuts_user_prompt_embeds_hook_json():
    prompt = build_stage2_timing_cuts_user_prompt(
        stage1_json=_STAGE1_STUB,
        timing_analysis_json={
            "selected_rule": "Dynamic Contrast",
            "reason": "x",
            "raw_timings": {
                "kick_bass": [], "snare_clap": [], "vocal_phrases": [], "semantic_peaks": [],
            },
        },
        bpm=120.0,
        fast_start_seconds=5.0,
        timing_mode="hook_aware",
        hook_analysis=_HOOK_FIXTURE,
    )
    assert "HOOK_ANALYSIS_JSON:" in prompt
    assert "TIMING_ANALYSIS_JSON:" in prompt


def test_hybrid_mode_omits_hook_block_when_kw_absent():
    prompt = build_stage2_timing_analysis_user_prompt(
        stage1_json=_STAGE1_STUB,
        subtitles_json=_SUBS_STUB,
        bpm=120.0,
        fast_start_seconds=5.0,
        timing_mode="hybrid",
    )
    assert "HOOK_ANALYSIS_JSON:" not in prompt
    assert "bpm_librosa" in prompt  # existing field still there


def test_prompts_mode_does_not_inject_hook_module_into_system():
    text = build_stage2_timing_analysis_system_instruction(timing_mode="prompts")
    assert "HOOK_AWARE module" not in text


def test_invalid_timing_mode_rejected():
    with pytest.raises(ValueError, match="Unsupported timing_mode"):
        build_stage2_timing_analysis_system_instruction(timing_mode="bogus")


def test_hook_analysis_kw_optional_in_hook_aware_mode():
    """
    The kw is optional in the builder signature so that callers that decide
    NOT to pass it (e.g. cold resume from cache) don't break — the system
    instruction will still inform LLM of the contract.
    """
    prompt = build_stage2_timing_analysis_user_prompt(
        stage1_json=_STAGE1_STUB,
        subtitles_json=_SUBS_STUB,
        bpm=120.0,
        fast_start_seconds=5.0,
        timing_mode="hook_aware",
    )
    assert "HOOK_ANALYSIS_JSON:" not in prompt
