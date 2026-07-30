from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from mlcore.alignment.core import (
    AlignedWord,
    AlignmentResult,
    EmissionTimeline,
    _build_stage1_asr,
    aggregate_words,
    align_targets_in_window,
    build_targets,
    ctc_viterbi_align,
    render_word_srt,
    reference_words,
    serialize_alignment_result,
    AlignmentFailure,
    ERROR_UNSUPPORTED_TEXT,
)


def _log_probs(rows: list[list[float]]) -> np.ndarray:
    return np.log(np.asarray(rows, dtype=np.float64))


class _RussianCharacterTokenizer:
    def __init__(self) -> None:
        alphabet = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
        self._vocab = {"<unk>": 0, "|": 1}
        self._vocab.update(
            {character: index for index, character in enumerate(alphabet, start=2)}
        )
        self.word_delimiter_token_id = 1
        self.word_delimiter_token = "|"
        self.unk_token_id = 0

    def get_vocab(self) -> dict[str, int]:
        return dict(self._vocab)

    def __call__(self, text: str, *, add_special_tokens: bool) -> SimpleNamespace:
        assert add_special_tokens is False
        return SimpleNamespace(
            input_ids=[self._vocab.get(character, 0) for character in text]
        )


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


def test_emission_timeline_constrains_tokens_to_user_window() -> None:
    timeline = EmissionTimeline(
        analysis_start_abs=9.5,
        sample_rate=16_000,
        input_samples=11 * 16_000,
        emission_frames=550,
        inputs_to_logits_ratio=320,
    )

    start_frame, end_frame = timeline.constrained_frame_range(
        clip_start_abs=10.0,
        clip_end_abs=20.0,
    )

    assert (start_frame, end_frame) == (25, 525)
    assert timeline.frame_to_abs(start_frame) == pytest.approx(10.0)
    assert timeline.frame_to_abs(end_frame) == pytest.approx(20.0)


def test_alignment_ignores_stronger_token_peaks_in_context_padding() -> None:
    emissions = _log_probs(
        [
            [0.05, 0.90, 0.03],  # stronger target peak in left context
            [0.90, 0.05, 0.05],
            [0.90, 0.05, 0.05],  # user window starts here
            [0.10, 0.80, 0.10],
            [0.90, 0.05, 0.05],
            [0.10, 0.10, 0.80],
            [0.90, 0.05, 0.05],  # user window ends here
            [0.03, 0.03, 0.94],  # stronger target peak in right context
        ]
    )
    timeline = EmissionTimeline(
        analysis_start_abs=9.96,
        sample_rate=16_000,
        input_samples=8 * 320,
        emission_frames=8,
        inputs_to_logits_ratio=320,
    )

    spans, _, start_frame, end_frame = align_targets_in_window(
        emissions,
        [1, 2],
        blank_id=0,
        timeline=timeline,
        clip_start_abs=10.0,
        clip_end_abs=10.1,
    )

    assert (start_frame, end_frame) == (2, 7)
    assert [(span.start_frame, span.end_frame) for span in spans] == [(1, 2), (3, 4)]
    absolute = [
        (
            timeline.frame_to_abs(start_frame + span.start_frame),
            timeline.frame_to_abs(start_frame + span.end_frame),
        )
        for span in spans
    ]
    assert absolute[0] == pytest.approx((10.02, 10.04))
    assert absolute[1] == pytest.approx((10.06, 10.08))


def test_ctc_viterbi_trims_weak_path_occupancy_to_posterior_evidence() -> None:
    emissions = _log_probs(
        [
            [0.05, 0.90, 0.03, 0.02],
            [0.01, 0.08, 0.01, 0.90],
            [0.01, 0.08, 0.01, 0.90],
            [0.01, 0.08, 0.01, 0.90],
            [0.01, 0.08, 0.01, 0.90],
            [0.05, 0.02, 0.90, 0.03],
            [0.90, 0.03, 0.05, 0.02],
        ]
    )

    spans, _ = ctc_viterbi_align(emissions, [1, 2], blank_id=0)

    assert spans[0].path_start_frame == 0
    assert spans[0].path_end_frame > spans[0].end_frame
    assert (spans[0].start_frame, spans[0].end_frame) == (0, 1)
    assert (spans[1].start_frame, spans[1].end_frame) == (5, 6)


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


def test_explicit_latin_pronunciations_keep_original_display_words() -> None:
    normalized, target_ids, token_word_indexes = build_targets(
        display_words=["на", "iPhone", "Samson"],
        pronunciation_words=["на", "айфон", "самсон"],
        tokenizer=_RussianCharacterTokenizer(),
    )

    assert normalized == ["на", "айфон", "самсон"]
    assert 0 not in target_ids
    assert set(token_word_indexes) == {-1, 0, 1, 2}


def test_unknown_latin_word_fails_with_actionable_word_name() -> None:
    with pytest.raises(AlignmentFailure) as exc:
        build_targets(
            display_words=["на", "Spotify"],
            pronunciation_words=["на", "spotify"],
            tokenizer=_RussianCharacterTokenizer(),
        )

    assert exc.value.code == ERROR_UNSUPPORTED_TEXT
    assert "Spotify" in exc.value.message
    assert "fix the pronunciation normalizer" in exc.value.message


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
