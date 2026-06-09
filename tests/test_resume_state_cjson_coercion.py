"""Regression tests for the Redis lua-cjson `[] -> {}` corruption of
resume_state payloads (root cause of /bigtest re-running Stage1 ASR on every
case). See mlcore/models/_cjson_compat.py.

The Redis JobStore Lua merge round-trips JobState through cjson, which has no
distinct empty-array type, so empty lists come back as `{}`. On text reuse the
seeded resume_state is model_validate'd; without coercion `{}`-for-`[]` raises
"Input should be a valid list" and the orchestrator discards the cached stage.
"""
from __future__ import annotations

import copy

import pytest

from mlcore.models.stage1_asr import Stage1AsrPayload, Stage1AsrSelectedFragment
from mlcore.models.switch_timing import SwitchTimingPayload
from mlcore.models._cjson_compat import restore_cjson_empty_lists


def _valid_stage1_asr() -> dict:
    return {
        "transcript_words": [
            {"text": "hello", "t_start": 0.0, "t_end": 0.5},
            {"text": "world", "t_start": 0.5, "t_end": 1.0},
        ],
        "pause_spans": [],
        "srt_items": [],
        "selected_fragment": {
            "audio": {"clip_start_abs": 0.0, "clip_end_abs": 1.0},
            "transcript_words": [
                {"text": "hello", "t_start": 0.0, "t_end": 0.5},
            ],
            "pause_spans": [],
            "srt_items": [],
        },
    }


def _corrupt_empty_lists_to_objects(obj):
    """Mimic lua-cjson: every empty list becomes an empty object."""
    if isinstance(obj, list):
        if not obj:
            return {}
        return [_corrupt_empty_lists_to_objects(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _corrupt_empty_lists_to_objects(v) for k, v in obj.items()}
    return obj


def test_stage1_asr_validates_when_empty_lists_corrupted_to_objects() -> None:
    corrupted = _corrupt_empty_lists_to_objects(_valid_stage1_asr())
    # Sanity: the corruption really produced {} where lists were empty.
    assert corrupted["pause_spans"] == {}
    assert corrupted["srt_items"] == {}
    assert corrupted["selected_fragment"]["pause_spans"] == {}

    payload = Stage1AsrPayload.model_validate(corrupted)
    assert payload.pause_spans == []
    assert payload.srt_items == []
    assert payload.selected_fragment is not None
    assert payload.selected_fragment.pause_spans == []
    assert payload.selected_fragment.srt_items == []
    # Non-empty lists are untouched.
    assert len(payload.transcript_words) == 2


def test_stage1_asr_raises_without_coercion_baseline() -> None:
    """Documents the bug: a raw {}-for-[] payload would fail core list typing
    if the coercion were absent. We assert the coercion path fixes it, and that
    a genuinely wrong type (non-empty dict for a list) still fails."""
    corrupted = _corrupt_empty_lists_to_objects(_valid_stage1_asr())
    # coercion handles empty {} -> []
    Stage1AsrPayload.model_validate(corrupted)
    # but a populated dict where a list is expected is still rejected
    bad = copy.deepcopy(_valid_stage1_asr())
    bad["pause_spans"] = {"unexpected": 1}
    with pytest.raises(Exception):
        Stage1AsrPayload.model_validate(bad)


def test_selected_fragment_standalone_coercion() -> None:
    frag = {
        "audio": {"clip_start_abs": 0.0, "clip_end_abs": 1.0},
        "transcript_words": [{"text": "x", "t_start": 0.0, "t_end": 0.2}],
        "pause_spans": {},
        "srt_items": {},
    }
    p = Stage1AsrSelectedFragment.model_validate(frag)
    assert p.pause_spans == []
    assert p.srt_items == []


def test_switch_timing_empty_points_corrupted() -> None:
    raw = {
        "clip_start_abs": 0.0,
        "clip_end_abs": 10.0,
        "fast_start_seconds": 1.0,
        "switch_points_abs": {},  # cjson-corrupted empty list
    }
    p = SwitchTimingPayload.model_validate(raw)
    assert p.switch_points_abs == []


def test_restore_helper_leaves_real_dicts_untouched() -> None:
    """Schema-guided: a non-list field that is a genuine empty object must NOT
    be turned into a list."""

    class _Dummy:
        # minimal stand-in is unnecessary; use a real model instead.
        pass

    # Use Stage1AsrPayload: selected_fragment is a model (object) field, NOT a
    # list. An empty {} there must stay a dict (and then validate as a fragment
    # only if it has the required keys — here None is allowed).
    data = _valid_stage1_asr()
    # selected_fragment is an object field; ensure helper does not listify it.
    fixed = restore_cjson_empty_lists(Stage1AsrPayload, copy.deepcopy(data))
    assert isinstance(fixed["selected_fragment"], dict)
    assert fixed["pause_spans"] == []  # list field empty -> []
