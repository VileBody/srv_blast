from __future__ import annotations

import pytest

from mlcore.bench_asr_v2 import (
    best_subsequence_alignment,
    compute_global_sdi,
    normalize_word,
    normalized_words_from_text,
    validate_forced_alignment_strict,
)
from mlcore.models.stage1_forced_alignment import Stage1ForcedAlignmentPayload


def test_normalization_lower_trim_and_yo() -> None:
    assert normalize_word("Ёлка,") == "елка"
    assert normalize_word("...МИР!!!") == "мир"
    assert normalized_words_from_text("Привет,   мир!") == ["привет", "мир"]


def test_global_alignment_sdi_extraction() -> None:
    ref = ["a", "b", "c"]
    hyp = ["a", "x", "c", "z"]
    s, d, i = compute_global_sdi(ref, hyp)
    assert (s, d, i) == (1, 0, 1)


def test_best_subsequence_alignment_for_v1_stream() -> None:
    ref = ["beta", "gamma", "delta"]
    hyp = ["foo", "beta", "oops", "delta", "bar"]
    best = best_subsequence_alignment(ref, hyp)
    assert best.start_idx == 1
    assert best.end_idx == 3
    assert best.substitutions == 1
    assert best.deletions == 0
    assert best.insertions == 0


def test_validate_forced_alignment_strict() -> None:
    payload = Stage1ForcedAlignmentPayload.model_validate(
        {
            "aligned_words": [
                {"text": "Привет,", "t_start": 0.10, "t_end": 0.30},
                {"text": "мир!", "t_start": 0.31, "t_end": 0.55},
            ]
        }
    )
    out, warnings = validate_forced_alignment_strict(payload, ["привет", "мир"])
    assert len(out) == 2
    assert warnings == []

    out2, warnings2 = validate_forced_alignment_strict(
        {"aligned_words": [{"text": "привет", "t_start": 0.0, "t_end": 0.2}]},
        ["привет", "мир"],
    )
    assert len(out2) == 1
    assert warnings2 and "count mismatch" in warnings2[0]

    with pytest.raises(ValueError):
        validate_forced_alignment_strict(
            {
                "aligned_words": [
                    {"text": "привет", "t_start": 0.2, "t_end": 0.2},
                    {"text": "мир", "t_start": 0.3, "t_end": 0.5},
                ]
            },
            ["привет", "мир"],
        )
