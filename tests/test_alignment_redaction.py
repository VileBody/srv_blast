from __future__ import annotations

import subprocess
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mlcore.alignment.api import create_app
from mlcore.alignment.contracts import (
    ERROR_FULLY_REDACTED_WORD,
    ERROR_UNSUPPORTED_TEXT,
    ERROR_WINDOW_MISMATCH,
)
from mlcore.alignment.core import (
    WILDCARD_NON_BLANK_WEIGHT,
    WILDCARD_TARGET_ID,
    AlignedWord,
    AlignmentFailure,
    EmissionTimeline,
    _build_stage1_asr,
    aggregate_words,
    align_targets_in_window,
    build_targets,
    ctc_viterbi_align,
    reference_words,
    wildcard_emission_column,
)
from mlcore.alignment.pronunciation import EspeakEnglishToRussianNormalizer
from mlcore.alignment.redaction import ALIGNMENT_WILDCARD, REDACTION_MARKERS


# --------------------------------------------------------------------------
# Fixtures / doubles
# --------------------------------------------------------------------------


class _FakeEspeak:
    def __init__(self, pronunciations: dict[str, str]):
        self.pronunciations = pronunciations
        self.phoneme_calls: list[str] = []

    def __call__(self, command, **_kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="eSpeak NG text-to-speech: 1.52.0\n",
                stderr="",
            )
        word = command[-1]
        self.phoneme_calls.append(word)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=self.pronunciations[word] + "\n",
            stderr="",
        )


def _normalizer(
    pronunciations: dict[str, str] | None = None,
) -> tuple[EspeakEnglishToRussianNormalizer, _FakeEspeak]:
    runner = _FakeEspeak(pronunciations or {})
    normalizer = EspeakEnglishToRussianNormalizer(
        espeak_bin="espeak-ng",
        voice="en-us",
        expected_version="1.52.0",
        timeout_s=2.0,
        overrides={},
        runner=runner,
    )
    return normalizer, runner


class _FakeRuntime:
    """Minimal alignment runtime double for HTTP contract assertions."""

    def __init__(self, *, failure: AlignmentFailure):
        self.failure = failure
        self.ready = True

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def status(self) -> dict:
        return {"ready": True, "model_revision": "rev-test", "load_error": ""}

    async def align(self, **_kwargs):
        raise self.failure


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


# Synthetic 4-symbol acoustic model: 0=blank, 1="к", 2="р", 3="у" (hidden).
_BLANK_ROW = [0.91, 0.03, 0.03, 0.03]
_K_ROW = [0.07, 0.90, 0.02, 0.01]
_R_ROW = [0.07, 0.02, 0.90, 0.01]
_HIDDEN_ROW = [0.07, 0.02, 0.01, 0.90]


def _log_probs(rows: list[list[float]]) -> np.ndarray:
    return np.log(np.asarray(rows, dtype=np.float64))


# --------------------------------------------------------------------------
# Tokenisation of the user text (display tokens)
# --------------------------------------------------------------------------


def test_reference_words_keeps_masks_and_still_drops_real_punctuation() -> None:
    assert reference_words("Я курю с*г*рету, бл**ь!") == [
        "Я",
        "курю",
        "с*г*рету",
        "бл**ь",
    ]


def test_reference_words_keeps_edge_masks_inside_quotes() -> None:
    assert reference_words("«*уй!» — сказал х*й*") == ["*уй", "сказал", "х*й*"]


def test_reference_words_keeps_standalone_mask_as_a_word() -> None:
    # It is not silently dropped: the fully-masked contract fails later, loudly.
    assert reference_words("дай *** сюда") == ["дай", "***", "сюда"]


# --------------------------------------------------------------------------
# Normalizer: display token vs alignment token
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("display", "alignment", "wildcards"),
    [
        ("К*р", "к*р", 1),
        ("х*й", "х*й", 1),
        ("бл**ь", "бл*ь", 1),
        ("п***ц", "п*ц", 1),
        ("с*г*рету", "с*г*рету", 2),
        ("*уй", "*уй", 1),
        ("бля*", "бля*", 1),
        ("*бля*", "*бля*", 2),
    ],
)
def test_masked_words_keep_display_and_get_one_wildcard_per_marker_run(
    display: str,
    alignment: str,
    wildcards: int,
) -> None:
    normalizer, runner = _normalizer()

    word = normalizer.normalize_word(display)

    assert word.display_text == display
    assert word.alignment_text == alignment
    assert word.wildcard_count == wildcards
    assert word.strategy == "redacted_literal_cyrillic"
    assert runner.phoneme_calls == []


@pytest.mark.parametrize("marker", sorted(REDACTION_MARKERS))
def test_every_allowed_unicode_marker_maps_to_the_canonical_wildcard(
    marker: str,
) -> None:
    normalizer, _ = _normalizer()

    word = normalizer.normalize_word(f"К{marker}р")

    assert word.alignment_text == f"к{ALIGNMENT_WILDCARD}р"
    assert word.wildcard_count == 1


def test_fully_masked_word_fails_with_a_dedicated_error() -> None:
    normalizer, _ = _normalizer()

    with pytest.raises(AlignmentFailure) as exc:
        normalizer.normalize_word("***")

    assert exc.value.code == ERROR_FULLY_REDACTED_WORD
    assert "***" in exc.value.message
    assert "at least one visible letter" in exc.value.message


def test_truly_unsupported_character_still_fails_as_unsupported_text() -> None:
    normalizer, _ = _normalizer()

    with pytest.raises(AlignmentFailure) as exc:
        normalizer.normalize_word("К#р")

    assert exc.value.code == ERROR_UNSUPPORTED_TEXT
    assert "'#'" in exc.value.message


def test_masked_word_next_to_english_word_keeps_both_representations() -> None:
    normalizer, runner = _normalizer({"My": "mˈa‍ɪ"})

    words = normalizer.normalize_words(["My", "х*й"])

    assert [word.display_text for word in words] == ["My", "х*й"]
    assert [word.alignment_text for word in words] == ["май", "х*й"]
    assert [word.strategy for word in words] == [
        "espeak_en",
        "redacted_literal_cyrillic",
    ]
    assert runner.phoneme_calls == ["My"]


def test_masked_latin_word_is_phonemized_around_the_wildcard() -> None:
    normalizer, runner = _normalizer({"f": "ˈɛf", "ck": "kˈɪk"})

    word = normalizer.normalize_word("f*ck")

    assert word.display_text == "f*ck"
    assert word.alignment_text == "эф*кик"
    assert word.strategy == "redacted_espeak_en"
    assert runner.phoneme_calls == ["f", "ck"]


def test_plain_russian_text_is_untouched_by_the_redaction_pass() -> None:
    normalizer, runner = _normalizer()

    words = normalizer.normalize_words(["Привет", "мир"])

    assert [word.alignment_text for word in words] == ["Привет", "мир"]
    assert [word.strategy for word in words] == ["literal_cyrillic"] * 2
    assert [word.wildcard_count for word in words] == [0, 0]
    assert runner.phoneme_calls == []


def test_plain_english_text_is_untouched_by_the_redaction_pass() -> None:
    normalizer, runner = _normalizer(
        {"Hello": "həlˈo‍ʊ", "world": "wˈɜːld"}
    )

    words = normalizer.normalize_words(["Hello", "world"])

    assert [word.alignment_text for word in words] == ["халоу", "уэлд"]
    assert [word.strategy for word in words] == ["espeak_en"] * 2
    assert [word.wildcard_count for word in words] == [0, 0]
    assert runner.phoneme_calls == ["Hello", "world"]


# --------------------------------------------------------------------------
# CTC target construction
# --------------------------------------------------------------------------


def test_build_targets_emits_one_wildcard_unit_per_marker_run() -> None:
    normalized, target_ids, token_word_indexes = build_targets(
        display_words=["дай", "с*г*рету"],
        pronunciation_words=["дай", "с*г*рету"],
        tokenizer=_RussianCharacterTokenizer(),
    )

    assert normalized == ["дай", "с*г*рету"]
    assert target_ids.count(WILDCARD_TARGET_ID) == 2
    # Wildcards belong to their display word, so their frames extend it.
    wildcard_words = {
        word_index
        for token_id, word_index in zip(target_ids, token_word_indexes)
        if token_id == WILDCARD_TARGET_ID
    }
    assert wildcard_words == {1}
    assert 0 not in target_ids  # no <unk>


def test_build_targets_rejects_a_word_made_only_of_wildcards() -> None:
    with pytest.raises(AlignmentFailure) as exc:
        build_targets(
            display_words=["дай", "***"],
            pronunciation_words=["дай", "*"],
            tokenizer=_RussianCharacterTokenizer(),
        )

    assert exc.value.code == ERROR_FULLY_REDACTED_WORD
    assert "***" in exc.value.message


# --------------------------------------------------------------------------
# CTC alignment of the wildcard
# --------------------------------------------------------------------------


def test_wildcard_column_scores_speech_and_stays_below_blank_in_silence() -> None:
    emissions = _log_probs([_BLANK_ROW, _HIDDEN_ROW])

    column = wildcard_emission_column(emissions, blank_id=0)

    assert column[0] == pytest.approx(np.log(0.09 * WILDCARD_NON_BLANK_WEIGHT))
    assert column[1] == pytest.approx(np.log(0.93 * WILDCARD_NON_BLANK_WEIGHT))
    # In silence the blank state is cheaper, so the wildcard cannot park there.
    assert column[0] < emissions[0, 0]
    assert column[1] > emissions[1, 0]


def test_wildcard_owns_the_hidden_audio_between_known_letters() -> None:
    emissions = _log_probs(
        [
            _BLANK_ROW,
            _K_ROW,
            _K_ROW,
            _HIDDEN_ROW,  # masked letter, no known grapheme
            _HIDDEN_ROW,
            _R_ROW,
            _R_ROW,
            _BLANK_ROW,
        ]
    )

    spans, _ = ctc_viterbi_align(
        emissions,
        [1, WILDCARD_TARGET_ID, 2],
        blank_id=0,
    )

    assert [(span.start_frame, span.end_frame) for span in spans] == [
        (1, 3),
        (3, 5),
        (5, 7),
    ]
    assert [span.is_wildcard for span in spans] == [False, True, False]


def test_masked_word_timing_covers_the_hidden_audio() -> None:
    emissions = _log_probs(
        [
            _BLANK_ROW,
            _K_ROW,
            _HIDDEN_ROW,
            _HIDDEN_ROW,
            _R_ROW,
            _BLANK_ROW,
        ]
    )
    spans, _ = ctc_viterbi_align(
        emissions,
        [1, WILDCARD_TARGET_ID, 2],
        blank_id=0,
    )

    words = aggregate_words(
        display_words=["К*р"],
        normalized_words=["к*р"],
        spans=spans,
        token_word_indexes=[0, 0, 0],
        seconds_per_frame=0.02,
        analysis_start_abs=10.0,
    )

    assert len(words) == 1
    assert words[0].text == "К*р"  # display token survives
    # Frames 1..5: the whole spoken word including the two masked frames.
    assert words[0].t_start == pytest.approx(10.02)
    assert words[0].t_end == pytest.approx(10.10)
    # The wildcard carries no grapheme evidence and must not skew confidence.
    assert words[0].confidence == pytest.approx(0.90)


def test_wildcard_does_not_compress_the_visible_part_of_the_word() -> None:
    # Both "к" frames are strong. Without the non-blank discount the wildcard
    # would outbid every speech frame and squeeze "к" down to a single frame.
    emissions = _log_probs(
        [
            _BLANK_ROW,
            _K_ROW,
            _K_ROW,
            _HIDDEN_ROW,
            _HIDDEN_ROW,
            _BLANK_ROW,
        ]
    )

    spans, _ = ctc_viterbi_align(emissions, [1, WILDCARD_TARGET_ID], blank_id=0)

    assert (spans[0].start_frame, spans[0].end_frame) == (1, 3)
    assert (spans[1].start_frame, spans[1].end_frame) == (3, 5)


def test_leading_wildcard_does_not_steal_frames_from_the_previous_word() -> None:
    # vocab: 0=blank, 1="к", 2="р", 3=hidden, 4="|"
    blank = [0.90, 0.02, 0.03, 0.03, 0.02]
    k_row = [0.06, 0.90, 0.01, 0.02, 0.01]
    r_row = [0.06, 0.01, 0.90, 0.02, 0.01]
    hidden = [0.06, 0.01, 0.01, 0.90, 0.02]
    delim = [0.06, 0.01, 0.01, 0.02, 0.90]
    emissions = _log_probs(
        [blank, k_row, k_row, delim, hidden, r_row, blank]
    )

    spans, _ = ctc_viterbi_align(
        emissions,
        [1, 4, WILDCARD_TARGET_ID, 2],
        blank_id=0,
    )

    words = aggregate_words(
        display_words=["к", "*р"],
        normalized_words=["к", "*р"],
        spans=spans,
        token_word_indexes=[0, -1, 1, 1],
        seconds_per_frame=0.02,
        analysis_start_abs=0.0,
    )

    # "к" keeps both of its frames.
    assert (spans[0].start_frame, spans[0].end_frame) == (1, 3)
    assert words[0].t_end == pytest.approx(0.06)
    # The masked word starts at the hidden frame, not at the visible "р".
    assert words[1].t_start == pytest.approx(0.08)
    assert words[1].t_end == pytest.approx(0.12)


def test_window_validation_is_not_regressed_by_wildcards() -> None:
    emissions = _log_probs(
        [
            _R_ROW,  # stronger peak in the left context padding
            _BLANK_ROW,
            _K_ROW,  # user window starts here
            _HIDDEN_ROW,
            _R_ROW,
            _BLANK_ROW,  # user window ends here
            _R_ROW,  # stronger peak in the right context padding
        ]
    )
    timeline = EmissionTimeline(
        analysis_start_abs=9.96,
        sample_rate=16_000,
        input_samples=7 * 320,
        emission_frames=7,
        inputs_to_logits_ratio=320,
    )

    spans, _, start_frame, end_frame = align_targets_in_window(
        emissions,
        [1, WILDCARD_TARGET_ID, 2],
        blank_id=0,
        timeline=timeline,
        clip_start_abs=10.0,
        clip_end_abs=10.08,
    )

    assert (start_frame, end_frame) == (2, 6)
    assert [(span.start_frame, span.end_frame) for span in spans] == [
        (0, 1),
        (1, 2),
        (2, 3),
    ]
    assert max(span.end_frame for span in spans) <= end_frame - start_frame


def test_stage1_payload_keeps_the_masked_spelling_for_subtitles() -> None:
    payload = _build_stage1_asr(
        words=[AlignedWord("К*р", "к*р", 10.02, 10.10, 0.02, 0.10, 0.9)],
        target_fragment="К*р",
        clip_start_abs=10.0,
        clip_end_abs=11.0,
        pause_min_gap_sec=0.35,
    )

    assert payload.transcript_words[0].text == "К*р"
    assert payload.selected_fragment is not None
    assert payload.selected_fragment.fragment_analytics.target_fragment == "К*р"


def test_fully_masked_word_is_a_client_error_over_http() -> None:
    app = create_app(
        _FakeRuntime(
            failure=AlignmentFailure(
                ERROR_FULLY_REDACTED_WORD,
                "reference word '***' is fully masked",
            )
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/align",
            json={
                "audio_path": "/app/work/jobs/a/data/track.mp3",
                "target_fragment": "дай ***",
                "clip_start_abs": 10.0,
                "clip_end_abs": 20.0,
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ALIGNMENT_FULLY_REDACTED_WORD"


def test_empty_user_window_still_fails_before_any_wildcard_work() -> None:
    timeline = EmissionTimeline(
        analysis_start_abs=10.0,
        sample_rate=16_000,
        input_samples=3200,
        emission_frames=10,
        inputs_to_logits_ratio=320,
    )

    with pytest.raises(AlignmentFailure) as exc:
        timeline.constrained_frame_range(clip_start_abs=10.0, clip_end_abs=10.005)

    assert exc.value.code == ERROR_WINDOW_MISMATCH
