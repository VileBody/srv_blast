from __future__ import annotations

import math
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mlcore.alignment.core import (
    AlignedWord,
    AlignmentResult,
    DynamicWindowConfig,
    EmissionTimeline,
    WindowAlignmentCandidate,
    _build_stage1_asr,
    aggregate_words,
    align_target_fragment,
    align_targets_in_window,
    build_targets,
    ctc_viterbi_align,
    generate_dynamic_window_bounds,
    render_word_srt,
    reference_words,
    select_dynamic_alignment_window,
    serialize_alignment_result,
    AlignmentFailure,
    ERROR_UNSUPPORTED_TEXT,
    ERROR_WINDOW_MISMATCH,
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


def _dynamic_window_config() -> DynamicWindowConfig:
    return DynamicWindowConfig(
        max_adjust_sec=2.0,
        step_sec=0.5,
        min_edge_clearance_sec=0.15,
        stability_tolerance_sec=0.11,
        min_consensus_candidates=3,
        score_tolerance=0.12,
        min_boundary_duration_ratio=0.15,
    )


def _select_with_synthetic_boundary_evidence(
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_right_evidence: bool,
    include_left_evidence: bool = True,
    left_user_window_censored: bool = False,
    split_confidence_evidence: bool = False,
    include_right_confidence: bool = True,
):
    timeline = EmissionTimeline(
        analysis_start_abs=0.0,
        sample_rate=10,
        input_samples=61,
        emission_frames=61,
        inputs_to_logits_ratio=1,
    )
    words = (
        AlignedWord("one", "one", 2.1, 2.2, 0.0, 0.1, 0.9),
        AlignedWord("two", "two", 3.8, 3.9, 1.7, 1.8, 0.9),
    )
    base_probe = (2.0, 4.0)
    left_probe = (1.5, 4.0)
    right_probe = (2.0, 4.5)
    hard_valid_bounds = {base_probe, left_probe}
    if include_right_evidence:
        hard_valid_bounds.add(right_probe)

    def _candidate(**kwargs) -> WindowAlignmentCandidate:
        bounds = (
            round(float(kwargs["search_start_abs"]), 1),
            round(float(kwargs["search_end_abs"]), 1),
        )
        hard_valid = bounds in hard_valid_bounds
        left_supported = bounds == left_probe and include_left_evidence
        right_supported = bounds == right_probe and include_right_evidence
        left_confidence_supported = (
            bounds != right_probe if split_confidence_evidence else True
        )
        right_confidence_supported = (
            (bounds == right_probe if split_confidence_evidence else True)
            and include_right_confidence
        )
        reasons_list = [] if hard_valid else ["outside_user_window"]
        if hard_valid and not (left_supported and right_supported):
            reasons_list.append("insufficient_edge_clearance")
        if not left_confidence_supported:
            reasons_list.append("low_left_boundary_word_confidence")
        if not right_confidence_supported:
            reasons_list.append("low_right_boundary_word_confidence")
        reasons = tuple(reasons_list)
        return WindowAlignmentCandidate(
            search_start_abs=bounds[0],
            search_end_abs=bounds[1],
            search_start_frame=int(bounds[0] * 10),
            search_end_frame=int(bounds[1] * 10),
            token_spans=(),
            words=words,
            path_log_score=-1.0,
            mean_word_confidence=0.9,
            min_word_confidence=0.9,
            boundary_word_confidence=0.9,
            left_boundary_word_confidence=(
                0.9 if left_confidence_supported else 0.01
            ),
            right_boundary_word_confidence=(
                0.9 if right_confidence_supported else 0.01
            ),
            left_confidence_supported=left_confidence_supported,
            right_confidence_supported=right_confidence_supported,
            path_mean_probability=0.9,
            left_edge_clearance_sec=0.5 if left_supported else 0.0,
            right_edge_clearance_sec=0.5 if right_supported else 0.0,
            left_edge_supported=left_supported,
            right_edge_supported=right_supported,
            left_user_window_censored=left_user_window_censored,
            right_user_window_censored=False,
            boundary_duration_ratio=1.0,
            path_to_evidence_ratio=1.0,
            adjustment_sec=abs(bounds[0] - 2.0) + abs(bounds[1] - 4.0),
            quality_score=0.9,
            rejection_reasons=reasons,
        )

    monkeypatch.setattr("mlcore.alignment.core._build_window_candidate", _candidate)
    return select_dynamic_alignment_window(
        log_probs=np.log(np.full((61, 3), 1.0 / 3.0, dtype=np.float64)),
        target_ids=[1, 2],
        token_word_indexes=[0, 1],
        display_words=["one", "two"],
        normalized_words=["one", "two"],
        blank_id=0,
        timeline=timeline,
        clip_start_abs=2.0,
        clip_end_abs=4.0,
        config=DynamicWindowConfig(
            max_adjust_sec=0.5,
            step_sec=0.5,
            min_edge_clearance_sec=0.15,
            stability_tolerance_sec=0.11,
            min_consensus_candidates=3,
            score_tolerance=0.12,
            min_boundary_duration_ratio=0.15,
        ),
        min_word_confidence=0.5,
    )


def test_dynamic_window_combines_independent_boundary_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _select_with_synthetic_boundary_evidence(
        monkeypatch,
        include_right_evidence=True,
    )

    assert selection.diagnostics["eligible_candidate_count"] == 0
    assert selection.diagnostics["hard_valid_candidate_count"] == 3
    assert selection.diagnostics["consensus_candidate_count"] == 3
    assert selection.diagnostics["boundary_evidence_mode"] == "split_consensus"
    assert selection.diagnostics["left_edge_supported_candidate_count"] == 1
    assert selection.diagnostics["right_edge_supported_candidate_count"] == 1


def test_dynamic_window_rejects_consensus_without_right_boundary_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AlignmentFailure) as exc:
        _select_with_synthetic_boundary_evidence(
            monkeypatch,
            include_right_evidence=False,
        )

    assert exc.value.code == ERROR_WINDOW_MISMATCH
    assert "timing is unstable" in exc.value.message
    assert exc.value.details["hard_valid_candidate_count"] == 2
    assert exc.value.details["right_edge_supported_candidate_count"] == 0
    assert exc.value.details["top_candidates"]


def test_dynamic_window_accepts_confidence_from_independent_sides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _select_with_synthetic_boundary_evidence(
        monkeypatch,
        include_right_evidence=True,
        split_confidence_evidence=True,
    )

    assert selection.diagnostics["consensus_candidate_count"] == 3
    assert selection.diagnostics["left_confidence_supported_candidate_count"] == 2
    assert selection.diagnostics["right_confidence_supported_candidate_count"] == 1


def test_dynamic_window_uses_stable_timing_without_right_confidence_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _select_with_synthetic_boundary_evidence(
        monkeypatch,
        include_right_evidence=True,
        include_right_confidence=False,
    )

    assert selection.diagnostics["boundary_evidence_mode"] == "timing_consensus"
    assert selection.diagnostics["right_confidence_supported_candidate_count"] == 0
    assert (
        "low_right_boundary_word_confidence"
        in selection.diagnostics["boundary_evidence_warnings"]
    )


def test_dynamic_window_accepts_confident_user_window_censored_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = _select_with_synthetic_boundary_evidence(
        monkeypatch,
        include_right_evidence=True,
        include_left_evidence=False,
        left_user_window_censored=True,
    )

    assert selection.diagnostics["boundary_evidence_mode"] == "user_window_censored"
    assert selection.diagnostics["left_edge_supported_candidate_count"] == 0
    assert (
        selection.diagnostics["left_user_window_censored_candidate_count"]
        == 3
    )


def test_dynamic_window_candidates_are_bounded_and_include_boundary_probes() -> None:
    candidates = generate_dynamic_window_bounds(
        clip_start_abs=10.0,
        clip_end_abs=20.0,
        max_adjust_sec=2.0,
        step_sec=0.5,
    )

    assert len(candidates) <= 25
    assert (10.0, 20.0) in candidates
    assert (8.0, 22.0) in candidates
    assert (8.0, 18.0) in candidates
    assert (12.0, 22.0) in candidates


def test_dense_local_window_policy_keeps_three_neighbor_scales() -> None:
    candidates = generate_dynamic_window_bounds(
        clip_start_abs=10.0,
        clip_end_abs=20.0,
        max_adjust_sec=1.0,
        step_sec=0.25,
    )

    assert len(candidates) <= 25
    assert (9.75, 20.0) in candidates
    assert (9.5, 20.0) in candidates
    assert (9.0, 20.0) in candidates
    assert (9.0, 21.0) in candidates


def test_dynamic_window_expands_edges_and_uses_stable_acoustic_timings() -> None:
    probabilities = np.full((61, 3), 0.03, dtype=np.float64)
    probabilities[:, 0] = 0.94
    probabilities[21] = [0.05, 0.90, 0.05]
    probabilities[39] = [0.05, 0.05, 0.90]
    timeline = EmissionTimeline(
        analysis_start_abs=0.0,
        sample_rate=10,
        input_samples=61,
        emission_frames=61,
        inputs_to_logits_ratio=1,
    )

    selection = select_dynamic_alignment_window(
        log_probs=np.log(probabilities),
        target_ids=[1, 2],
        token_word_indexes=[0, 1],
        display_words=["раз", "два"],
        normalized_words=["раз", "два"],
        blank_id=0,
        timeline=timeline,
        clip_start_abs=2.0,
        clip_end_abs=4.0,
        config=_dynamic_window_config(),
        min_word_confidence=0.5,
    )

    assert selection.selected.search_start_abs < 2.0
    assert selection.selected.search_end_abs > 4.0
    assert [(word.t_start, word.t_end) for word in selection.selected.words] == [
        pytest.approx((2.1, 2.2)),
        pytest.approx((3.9, 4.0)),
    ]
    assert selection.diagnostics["consensus_candidate_count"] >= 3
    assert selection.diagnostics["max_timing_deviation_sec"] <= 0.11
    assert selection.diagnostics["mode"] == "single_inference_multi_window_consensus"


def test_dynamic_window_uses_edge_probes_but_selects_clear_candidate() -> None:
    probabilities = np.full((61, 3), 0.03, dtype=np.float64)
    probabilities[:, 0] = 0.94
    probabilities[21] = [0.05, 0.90, 0.05]
    probabilities[39] = [0.05, 0.05, 0.90]
    timeline = EmissionTimeline(
        analysis_start_abs=0.0,
        sample_rate=10,
        input_samples=61,
        emission_frames=61,
        inputs_to_logits_ratio=1,
    )
    config = DynamicWindowConfig(
        max_adjust_sec=0.5,
        step_sec=0.5,
        min_edge_clearance_sec=0.15,
        stability_tolerance_sec=0.11,
        min_consensus_candidates=3,
        score_tolerance=0.12,
        min_boundary_duration_ratio=0.15,
    )

    selection = select_dynamic_alignment_window(
        log_probs=np.log(probabilities),
        target_ids=[1, 2],
        token_word_indexes=[0, 1],
        display_words=["раз", "два"],
        normalized_words=["раз", "два"],
        blank_id=0,
        timeline=timeline,
        clip_start_abs=2.0,
        clip_end_abs=4.0,
        config=config,
        min_word_confidence=0.5,
    )

    assert selection.diagnostics["eligible_candidate_count"] == 1
    assert selection.diagnostics["edge_probe_candidate_count"] >= 2
    assert selection.diagnostics["consensus_candidate_count"] >= 3
    assert selection.selected.rejection_reasons == ()
    assert selection.selected.left_edge_clearance_sec >= 0.15
    assert selection.selected.right_edge_clearance_sec >= 0.15


def test_dynamic_window_warns_on_weak_interior_word_without_rejecting() -> None:
    probabilities = np.full((61, 4), 0.02, dtype=np.float64)
    probabilities[:, 0] = 0.94
    probabilities[21] = [0.05, 0.90, 0.025, 0.025]
    probabilities[30] = [0.80, 0.05, 0.10, 0.05]
    probabilities[39] = [0.05, 0.025, 0.025, 0.90]
    timeline = EmissionTimeline(
        analysis_start_abs=0.0,
        sample_rate=10,
        input_samples=61,
        emission_frames=61,
        inputs_to_logits_ratio=1,
    )

    selection = select_dynamic_alignment_window(
        log_probs=np.log(probabilities),
        target_ids=[1, 2, 3],
        token_word_indexes=[0, 1, 2],
        display_words=["раз", "два", "три"],
        normalized_words=["раз", "два", "три"],
        blank_id=0,
        timeline=timeline,
        clip_start_abs=2.0,
        clip_end_abs=4.0,
        config=_dynamic_window_config(),
        min_word_confidence=0.5,
    )

    assert selection.selected.min_word_confidence < 0.5
    assert selection.selected.boundary_word_confidence >= 0.5
    assert selection.diagnostics["eligible_candidate_count"] >= 3
    assert (
        selection.diagnostics["policy"]["min_boundary_word_confidence"]
        == 0.5
    )


def test_dynamic_window_accepts_stable_weak_boundary_with_warning() -> None:
    probabilities = np.full((61, 4), 0.02, dtype=np.float64)
    probabilities[:, 0] = 0.94
    probabilities[21] = [0.80, 0.10, 0.05, 0.05]
    probabilities[30] = [0.05, 0.025, 0.90, 0.025]
    probabilities[39] = [0.05, 0.025, 0.025, 0.90]
    timeline = EmissionTimeline(
        analysis_start_abs=0.0,
        sample_rate=10,
        input_samples=61,
        emission_frames=61,
        inputs_to_logits_ratio=1,
    )

    selection = select_dynamic_alignment_window(
        log_probs=np.log(probabilities),
        target_ids=[1, 2, 3],
        token_word_indexes=[0, 1, 2],
        display_words=["one", "two", "three"],
        normalized_words=["one", "two", "three"],
        blank_id=0,
        timeline=timeline,
        clip_start_abs=2.0,
        clip_end_abs=4.0,
        config=_dynamic_window_config(),
        min_word_confidence=0.5,
    )

    assert selection.diagnostics["boundary_evidence_mode"] == "timing_consensus"
    assert selection.diagnostics["left_confidence_supported_candidate_count"] == 0
    assert (
        "low_left_boundary_word_confidence"
        in selection.diagnostics["boundary_evidence_warnings"]
    )


def test_dynamic_window_rejects_fragment_that_exceeds_user_clip() -> None:
    probabilities = np.full((61, 3), 0.03, dtype=np.float64)
    probabilities[:, 0] = 0.94
    probabilities[21] = [0.05, 0.90, 0.05]
    probabilities[42] = [0.05, 0.05, 0.90]
    timeline = EmissionTimeline(
        analysis_start_abs=0.0,
        sample_rate=10,
        input_samples=61,
        emission_frames=61,
        inputs_to_logits_ratio=1,
    )

    with pytest.raises(AlignmentFailure) as exc:
        select_dynamic_alignment_window(
            log_probs=np.log(probabilities),
            target_ids=[1, 2],
            token_word_indexes=[0, 1],
            display_words=["раз", "два"],
            normalized_words=["раз", "два"],
            blank_id=0,
            timeline=timeline,
            clip_start_abs=2.0,
            clip_end_abs=4.0,
            config=_dynamic_window_config(),
            min_word_confidence=0.5,
        )

    assert exc.value.code == ERROR_WINDOW_MISMATCH
    assert "boundary evidence is outside user window" in exc.value.message
    assert exc.value.details["rejection_counts"]["outside_user_window"] > 0
    assert exc.value.details["right_confidence_supported_candidate_count"] == 0
    assert exc.value.details["right_confident_outside_candidate_count"] >= 3


def test_dynamic_window_runs_separator_and_acoustic_model_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probabilities = np.full((61, 4), 0.02, dtype=np.float64)
    probabilities[:, 0] = 0.94
    probabilities[21] = [0.04, 0.90, 0.03, 0.03]
    probabilities[30] = [0.04, 0.03, 0.03, 0.90]
    probabilities[39] = [0.04, 0.03, 0.90, 0.03]

    class FakeTokenizer:
        word_delimiter_token_id = 3
        word_delimiter_token = "|"
        unk_token_id = None

        def get_vocab(self) -> dict[str, int]:
            return {"<pad>": 0, "а": 1, "б": 2, "|": 3}

        def __call__(self, text: str, *, add_special_tokens: bool) -> SimpleNamespace:
            assert add_special_tokens is False
            return SimpleNamespace(input_ids=[self.get_vocab()[text]])

    class FakeProcessor:
        tokenizer = FakeTokenizer()

        def __call__(self, *_args, **_kwargs) -> SimpleNamespace:
            return SimpleNamespace(input_values=object())

    class ArrayTensor:
        def __init__(self, values: np.ndarray):
            self.values = values

        def detach(self) -> "ArrayTensor":
            return self

        def cpu(self) -> "ArrayTensor":
            return self

        def numpy(self) -> np.ndarray:
            return self.values

    class FakeTorch:
        @staticmethod
        def inference_mode():
            return nullcontext()

        @staticmethod
        def log_softmax(_logits, *, dim: int) -> ArrayTensor:
            assert dim == -1
            return ArrayTensor(np.log(probabilities))

    class FakeModel:
        config = SimpleNamespace(pad_token_id=0, inputs_to_logits_ratio=1600)

        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, *_args, **_kwargs) -> SimpleNamespace:
            self.calls += 1
            return SimpleNamespace(logits=[object()])

    class FakeSeparator:
        input_sample_rate = 16_000
        input_channels = 1
        model_name = "fake-demucs"
        model_revision = "separator-rev"
        package_version = "4.1.0"

        def __init__(self) -> None:
            self.calls = 0

        def separate_vocals(self, _path: Path) -> SimpleNamespace:
            self.calls += 1
            return SimpleNamespace(
                waveform=np.ones(96_000, dtype=np.float32),
                sample_rate=16_000,
                diagnostics={"separator_model": self.model_name},
            )

    class FakePronunciationNormalizer:
        mode = "test"
        engine_version = "test"

        @staticmethod
        def normalize_words(words: list[str]) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(
                    display_text=word,
                    alignment_text=word,
                    strategy="literal_cyrillic",
                    ipa="",
                )
                for word in words
            ]

    monkeypatch.setattr(
        "mlcore.alignment.core.extract_analysis_crop",
        lambda **_kwargs: (0.0, 6.0),
    )
    audio_path = tmp_path / "track.wav"
    audio_path.write_bytes(b"audio")
    model = FakeModel()
    separator = FakeSeparator()

    result = align_target_fragment(
        audio_path=audio_path,
        target_fragment="а б",
        clip_start_abs=2.0,
        clip_end_abs=4.0,
        processor=FakeProcessor(),
        model=model,
        torch_module=FakeTorch(),
        vocal_separator=separator,
        pronunciation_normalizer=FakePronunciationNormalizer(),
        model_revision="model-rev",
        min_word_confidence=0.5,
        dynamic_window_max_adjust_sec=2.0,
        dynamic_window_step_sec=0.5,
        dynamic_window_min_edge_clearance_sec=0.15,
        dynamic_window_stability_tolerance_sec=0.11,
    )

    assert model.calls == 1
    assert separator.calls == 1
    assert result.diagnostics["dynamic_window"]["candidate_count"] > 1
    assert result.diagnostics["dynamic_window"]["consensus_candidate_count"] >= 3


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
