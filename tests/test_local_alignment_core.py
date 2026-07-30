from __future__ import annotations

import math

import numpy as np
import pytest

from mlcore.alignment.core import (
    AlignedWord,
    AlignmentResult,
    _build_stage1_asr,
    aggregate_words,
    clip_aligned_words_to_window,
    ctc_viterbi_align,
    render_word_srt,
    reference_words,
    serialize_alignment_result,
    AlignmentFailure,
    ERROR_UNSUPPORTED_TEXT,
)


def _log_probs(rows: list[list[float]]) -> np.ndarray:
    return np.log(np.asarray(rows, dtype=np.float64))


def test_reference_words_drops_structural_tags_and_edge_punctuation() -> None:
    assert reference_words("[verse] Привет, мир! [pause]") == ["Привет", "мир"]


def test_reference_words_rejects_unsupported_empty_text() -> None:
    with pytest.raises(AlignmentFailure) as exc:
        reference_words("[verse] [pause]")
    assert exc.value.code == ERROR_UNSUPPORTED_TEXT


def test_ctc_viterbi_aligns_two_tokens() -> None:
    emissions = _log_probs(
        [
            [0.90, 0.05, 0.05],
            [0.05, 0.90, 0.05],
            [0.90, 0.05, 0.05],
            [0.05, 0.05, 0.90],
            [0.90, 0.05, 0.05],
        ]
    )
    spans, score = ctc_viterbi_align(emissions, [1, 2], blank_id=0)
    assert math.isfinite(score)
    assert [(span.start_frame, span.end_frame) for span in spans] == [(1, 2), (3, 4)]


def test_ctc_viterbi_requires_blank_between_repeated_tokens() -> None:
    emissions = _log_probs(
        [
            [0.05, 0.95],
            [0.95, 0.05],
            [0.05, 0.95],
        ]
    )
    spans, _ = ctc_viterbi_align(emissions, [1, 1], blank_id=0)
    assert [(span.start_frame, span.end_frame) for span in spans] == [(0, 1), (2, 3)]


def test_aggregate_words_returns_absolute_timestamps() -> None:
    emissions = _log_probs(
        [
            [0.90, 0.05, 0.05],
            [0.05, 0.90, 0.05],
            [0.90, 0.05, 0.05],
            [0.05, 0.05, 0.90],
            [0.90, 0.05, 0.05],
        ]
    )
    spans, _ = ctc_viterbi_align(emissions, [1, 2], blank_id=0)
    words = aggregate_words(
        display_words=["раз", "два"],
        normalized_words=["РАЗ", "ДВА"],
        spans=spans,
        token_word_indexes=[0, 1],
        seconds_per_frame=0.02,
        analysis_start_abs=41.5,
    )
    assert words[0].t_start == 41.52
    assert words[0].t_end == 41.54
    assert words[1].t_start == 41.56
    assert words[1].t_end == 41.58


def test_stage1_adapter_keeps_acoustic_timings_and_derives_pauses() -> None:
    words = [
        AlignedWord("раз", "РАЗ", 10.1, 10.5, 0.1, 0.5, 0.9),
        AlignedWord("два", "ДВА", 11.0, 11.3, 1.0, 1.3, 0.8),
    ]
    payload = _build_stage1_asr(
        words=words,
        target_fragment="раз два",
        clip_start_abs=10.0,
        clip_end_abs=12.0,
        pause_min_gap_sec=0.35,
    )

    assert payload.transcript_words[0].t_start == 10.1
    assert payload.transcript_words[1].t_end == 11.3
    assert len(payload.pause_spans) == 1
    assert payload.pause_spans[0].t_start == 10.5
    assert payload.pause_spans[0].t_end == 11.0
    assert payload.selected_fragment is not None
    assert payload.selected_fragment.audio.clip_start_abs == 10.0
    assert payload.selected_fragment.audio.clip_end_abs == 12.0
    assert payload.selected_fragment.fragment_analytics is not None
    assert payload.selected_fragment.fragment_analytics.target_fragment == "раз два"
    assert payload.selected_fragment.fragment_analytics.working_start_abs == 10.0
    assert payload.selected_fragment.fragment_analytics.working_end_abs == 12.0


def test_boundary_words_are_clipped_without_moving_interior_timings() -> None:
    words = [
        AlignedWord("раз", "РАЗ", 9.8, 10.4, 0.3, 0.9, 0.9),
        AlignedWord("два", "ДВА", 10.8, 11.2, 1.3, 1.7, 0.8),
        AlignedWord("три", "ТРИ", 11.7, 12.3, 2.2, 2.8, 0.7),
    ]

    clipped, diagnostics = clip_aligned_words_to_window(
        words=words,
        clip_start_abs=10.0,
        clip_end_abs=12.0,
    )

    assert [(word.t_start, word.t_end) for word in clipped] == [
        (10.0, 10.4),
        (10.8, 11.2),
        (11.7, 12.0),
    ]
    assert (clipped[1].local_start, clipped[1].local_end) == (1.3, 1.7)
    assert clipped[0].local_start == pytest.approx(0.5)
    assert clipped[0].local_end == pytest.approx(0.9)
    assert clipped[2].local_start == pytest.approx(2.2)
    assert clipped[2].local_end == pytest.approx(2.5)
    assert [item["word_index"] for item in diagnostics] == [0, 2]


def test_word_outside_user_window_fails_explicitly() -> None:
    words = [AlignedWord("раз", "РАЗ", 9.0, 9.8, 0.0, 0.8, 0.9)]

    with pytest.raises(AlignmentFailure) as exc:
        clip_aligned_words_to_window(
            words=words,
            clip_start_abs=10.0,
            clip_end_abs=12.0,
        )

    assert exc.value.code == "ALIGNMENT_WINDOW_MISMATCH"


def test_alignment_json_and_srt_preserve_utf8() -> None:
    words = [AlignedWord("привет", "ПРИВЕТ", 10.1, 10.5, 0.1, 0.5, 0.9)]
    payload = _build_stage1_asr(
        words=words,
        target_fragment="привет",
        clip_start_abs=10.0,
        clip_end_abs=12.0,
        pause_min_gap_sec=0.35,
    )
    serialized = serialize_alignment_result(AlignmentResult(payload, {}, {}))
    srt = render_word_srt(words)

    assert '"text": "привет"' in serialized
    assert "\\u043f" not in serialized
    assert "00:00:10,100 --> 00:00:10,500\nпривет" in srt
