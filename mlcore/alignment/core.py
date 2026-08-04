from __future__ import annotations

import json
import math
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from mlcore.models.stage1_asr import Stage1AsrPayload

from .contracts import (
    ALIGNMENT_ALGORITHM_VERSION,
    AlignmentFailure,
    ERROR_INTERNAL,
    ERROR_MODEL_UNAVAILABLE,
    ERROR_TIMEOUT,
    ERROR_UNSUPPORTED_TEXT,
    ERROR_WINDOW_MISMATCH,
)
from .redaction import (
    ALIGNMENT_WILDCARD,
    REDACTION_MARKERS,
    UNIT_WILDCARD,
    alignment_units,
    count_wildcards,
    has_redaction,
    strip_edge_punctuation,
)

SAMPLE_RATE = 16_000
TOKEN_POSTERIOR_RELATIVE_FLOOR = 0.2
TOKEN_TO_BLANK_ODDS_FLOOR = 0.1

# Placeholder emitted by ``build_targets`` for a redaction wildcard. The real
# token id only exists once the emission matrix is known, so it is resolved in
# ``ctc_viterbi_align``.
WILDCARD_TARGET_ID = -1

# The wildcard column scores "some non-blank grapheme is emitted in this frame",
# discounted by this weight. A known grapheme that owns more than this share of
# the non-blank probability mass in a frame therefore always outbids the
# wildcard, which keeps the visible letters from being compressed while the
# wildcard still beats blank on the hidden phonemes.
WILDCARD_NON_BLANK_WEIGHT = 0.5


@dataclass(frozen=True)
class TokenSpan:
    target_index: int
    token_id: int
    start_frame: int
    end_frame: int
    score: float
    path_start_frame: int
    path_end_frame: int
    is_wildcard: bool = False


@dataclass(frozen=True)
class EmissionTimeline:
    """Exact mapping between absolute audio time and CTC emission frames."""

    analysis_start_abs: float
    sample_rate: int
    input_samples: int
    emission_frames: int
    inputs_to_logits_ratio: int

    @property
    def seconds_per_frame(self) -> float:
        return float(self.inputs_to_logits_ratio) / float(self.sample_rate)

    @property
    def analysis_end_abs(self) -> float:
        return float(self.analysis_start_abs) + (
            float(self.input_samples) / float(self.sample_rate)
        )

    def frame_to_abs(self, frame_index: int) -> float:
        return float(self.analysis_start_abs) + (
            float(frame_index) * self.seconds_per_frame
        )

    def constrained_frame_range(
        self,
        *,
        clip_start_abs: float,
        clip_end_abs: float,
    ) -> tuple[int, int]:
        """Return a half-open emission range fully contained in the user window."""
        start_offset = float(clip_start_abs) - float(self.analysis_start_abs)
        end_offset = float(clip_end_abs) - float(self.analysis_start_abs)
        frame_seconds = self.seconds_per_frame
        epsilon = frame_seconds * 1e-6
        start_frame = int(math.ceil((start_offset - epsilon) / frame_seconds))
        end_frame = int(math.floor((end_offset + epsilon) / frame_seconds))
        start_frame = max(0, min(start_frame, int(self.emission_frames)))
        end_frame = max(0, min(end_frame, int(self.emission_frames)))
        if end_frame <= start_frame:
            raise AlignmentFailure(
                ERROR_WINDOW_MISMATCH,
                "user window has no complete CTC emission frames",
            )
        return start_frame, end_frame


@dataclass(frozen=True)
class AlignedWord:
    text: str
    normalized_text: str
    t_start: float
    t_end: float
    local_start: float
    local_end: float
    confidence: float


@dataclass(frozen=True)
class AlignmentResult:
    stage1_asr: Stage1AsrPayload
    diagnostics: dict[str, Any]
    backend: dict[str, Any]


@dataclass(frozen=True)
class DynamicWindowConfig:
    max_adjust_sec: float
    step_sec: float
    min_edge_clearance_sec: float
    stability_tolerance_sec: float
    min_consensus_candidates: int
    score_tolerance: float
    min_boundary_duration_ratio: float

    def validate(self) -> None:
        if self.max_adjust_sec <= 0.0:
            raise AlignmentFailure(
                ERROR_INTERNAL,
                "dynamic window max adjustment must be positive",
            )
        if self.step_sec <= 0.0 or self.step_sec > self.max_adjust_sec:
            raise AlignmentFailure(
                ERROR_INTERNAL,
                "dynamic window step must be positive and not exceed max adjustment",
            )
        if self.min_edge_clearance_sec < 0.0:
            raise AlignmentFailure(
                ERROR_INTERNAL,
                "dynamic window edge clearance must be non-negative",
            )
        if self.stability_tolerance_sec <= 0.0:
            raise AlignmentFailure(
                ERROR_INTERNAL,
                "dynamic window stability tolerance must be positive",
            )
        if self.min_consensus_candidates < 2:
            raise AlignmentFailure(
                ERROR_INTERNAL,
                "dynamic window consensus must require at least two candidates",
            )
        if self.score_tolerance < 0.0 or self.score_tolerance > 1.0:
            raise AlignmentFailure(
                ERROR_INTERNAL,
                "dynamic window score tolerance must be in [0, 1]",
            )
        if not 0.0 <= self.min_boundary_duration_ratio <= 1.0:
            raise AlignmentFailure(
                ERROR_INTERNAL,
                "dynamic window boundary duration ratio must be in [0, 1]",
            )


@dataclass(frozen=True)
class WindowAlignmentCandidate:
    search_start_abs: float
    search_end_abs: float
    search_start_frame: int
    search_end_frame: int
    token_spans: tuple[TokenSpan, ...]
    words: tuple[AlignedWord, ...]
    path_log_score: float
    mean_word_confidence: float
    min_word_confidence: float
    boundary_word_confidence: float
    left_boundary_word_confidence: float
    right_boundary_word_confidence: float
    left_confidence_supported: bool
    right_confidence_supported: bool
    path_mean_probability: float
    left_edge_clearance_sec: float
    right_edge_clearance_sec: float
    left_edge_supported: bool
    right_edge_supported: bool
    left_user_window_censored: bool
    right_user_window_censored: bool
    boundary_duration_ratio: float
    path_to_evidence_ratio: float
    adjustment_sec: float
    quality_score: float
    rejection_reasons: tuple[str, ...]

    def summary(self) -> dict[str, Any]:
        return {
            "search_start_abs": float(self.search_start_abs),
            "search_end_abs": float(self.search_end_abs),
            "search_start_frame": int(self.search_start_frame),
            "search_end_frame": int(self.search_end_frame),
            "mean_word_confidence": float(self.mean_word_confidence),
            "min_word_confidence": float(self.min_word_confidence),
            "boundary_word_confidence": float(self.boundary_word_confidence),
            "left_boundary_word_confidence": float(
                self.left_boundary_word_confidence
            ),
            "right_boundary_word_confidence": float(
                self.right_boundary_word_confidence
            ),
            "left_confidence_supported": bool(self.left_confidence_supported),
            "right_confidence_supported": bool(self.right_confidence_supported),
            "path_mean_probability": float(self.path_mean_probability),
            "left_edge_clearance_sec": float(self.left_edge_clearance_sec),
            "right_edge_clearance_sec": float(self.right_edge_clearance_sec),
            "left_edge_supported": bool(self.left_edge_supported),
            "right_edge_supported": bool(self.right_edge_supported),
            "left_user_window_censored": bool(self.left_user_window_censored),
            "right_user_window_censored": bool(self.right_user_window_censored),
            "boundary_duration_ratio": float(self.boundary_duration_ratio),
            "path_to_evidence_ratio": float(self.path_to_evidence_ratio),
            "adjustment_sec": float(self.adjustment_sec),
            "quality_score": float(self.quality_score),
            "rejection_reasons": list(self.rejection_reasons),
        }


@dataclass(frozen=True)
class DynamicWindowSelection:
    selected: WindowAlignmentCandidate
    diagnostics: dict[str, Any]


def serialize_alignment_result(result: AlignmentResult) -> str:
    return json.dumps(
        {
            "stage1_asr": result.stage1_asr.model_dump(mode="json"),
            "diagnostics": result.diagnostics,
            "backend": result.backend,
        },
        ensure_ascii=False,
        indent=2,
    )


def format_srt_timestamp(seconds: float) -> str:
    total_ms = int(round(max(0.0, float(seconds)) * 1000.0))
    milliseconds = total_ms % 1000
    total_seconds = total_ms // 1000
    secs = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def render_word_srt(words: Sequence[AlignedWord]) -> str:
    lines: list[str] = []
    for index, word in enumerate(words, start=1):
        lines.extend(
            [
                str(index),
                f"{format_srt_timestamp(word.t_start)} --> "
                f"{format_srt_timestamp(word.t_end)}",
                word.text,
                "",
            ]
        )
    return "\n".join(lines)


def reference_words(text: str) -> list[str]:
    words: list[str] = []
    normalized = unicodedata.normalize("NFKC", str(text or "")).replace("\r", " ")
    for raw in normalized.split():
        if raw.startswith("[") and raw.endswith("]"):
            continue
        cleaned = strip_edge_punctuation(raw)
        if cleaned:
            words.append(cleaned)
    if not words:
        raise AlignmentFailure(
            ERROR_UNSUPPORTED_TEXT,
            "target_fragment is empty after normalization",
        )
    return words


def _run_checked(command: Sequence[str], *, timeout_s: float) -> None:
    try:
        completed = subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=float(timeout_s),
        )
    except subprocess.TimeoutExpired as exc:
        raise AlignmentFailure(
            ERROR_TIMEOUT,
            f"ffmpeg exceeded {float(timeout_s):.1f}s",
        ) from exc
    if completed.returncode != 0:
        tail = completed.stderr[-2000:].strip()
        raise AlignmentFailure(
            ERROR_INTERNAL,
            f"ffmpeg failed with code={completed.returncode}: {tail}",
        )


def extract_analysis_crop(
    *,
    ffmpeg_bin: str,
    audio_path: Path,
    output_path: Path,
    clip_start_abs: float,
    clip_end_abs: float,
    padding_left_sec: float,
    padding_right_sec: float,
    sample_rate: int = SAMPLE_RATE,
    channels: int = 1,
    timeout_s: float = 120.0,
) -> tuple[float, float]:
    if clip_start_abs < 0.0 or clip_end_abs <= clip_start_abs:
        raise AlignmentFailure(
            ERROR_WINDOW_MISMATCH,
            f"invalid clip window {clip_start_abs:.3f}..{clip_end_abs:.3f}",
        )
    if padding_left_sec < 0.0 or padding_right_sec < 0.0:
        raise AlignmentFailure(ERROR_INTERNAL, "analysis padding must be non-negative")
    if sample_rate <= 0 or channels <= 0:
        raise AlignmentFailure(ERROR_INTERNAL, "analysis audio format is invalid")
    if not audio_path.is_file():
        raise AlignmentFailure(ERROR_INTERNAL, "audio file is unavailable")

    analysis_start = max(0.0, float(clip_start_abs) - float(padding_left_sec))
    requested_end = float(clip_end_abs) + float(padding_right_sec)
    duration = requested_end - analysis_start
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run_checked(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{analysis_start:.6f}",
            "-i",
            str(audio_path),
            "-t",
            f"{duration:.6f}",
            "-vn",
            "-ac",
            str(int(channels)),
            "-ar",
            str(int(sample_rate)),
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ],
        timeout_s=float(timeout_s),
    )
    return analysis_start, requested_end


def wildcard_emission_column(log_probs: np.ndarray, *, blank_id: int) -> np.ndarray:
    """Log-probability that *some* unknown grapheme is emitted in each frame.

    This is the garbage ("star") model used for redacted letters: the hidden
    audio is known to be speech but its graphemes are unknown, so the wildcard
    scores the total non-blank probability mass, discounted by
    :data:`WILDCARD_NON_BLANK_WEIGHT`. Silence and inter-word gaps stay far
    cheaper on the blank state, so the wildcard cannot park itself outside the
    spoken word.
    """
    emissions = np.asarray(log_probs, dtype=np.float64)
    if emissions.ndim != 2:
        raise ValueError(f"log_probs must have shape [frames, vocab], got {emissions.shape}")
    frames, vocab_size = emissions.shape
    if blank_id < 0 or blank_id >= vocab_size:
        raise ValueError(f"blank_id is outside vocabulary: {blank_id}")
    if vocab_size < 2:
        raise AlignmentFailure(
            ERROR_MODEL_UNAVAILABLE,
            "model vocabulary has no non-blank tokens for the redaction wildcard",
        )
    non_blank = np.delete(emissions, blank_id, axis=1)
    row_max = np.max(non_blank, axis=1)
    finite = np.isfinite(row_max)
    column = np.full(frames, -np.inf, dtype=np.float64)
    if bool(np.any(finite)):
        shifted = non_blank[finite] - row_max[finite][:, None]
        column[finite] = row_max[finite] + np.log(np.sum(np.exp(shifted), axis=1))
    return column + math.log(WILDCARD_NON_BLANK_WEIGHT)


def ctc_viterbi_align(
    log_probs: np.ndarray,
    target_ids: Sequence[int],
    *,
    blank_id: int,
) -> tuple[list[TokenSpan], float]:
    emissions = np.asarray(log_probs, dtype=np.float64)
    if emissions.ndim != 2:
        raise ValueError(f"log_probs must have shape [frames, vocab], got {emissions.shape}")
    if not target_ids:
        raise ValueError("target_ids must not be empty")
    frames, vocab_size = emissions.shape
    targets = [int(token_id) for token_id in target_ids]
    if blank_id < 0 or blank_id >= vocab_size:
        raise ValueError(f"blank_id is outside vocabulary: {blank_id}")
    if any(token_id == WILDCARD_TARGET_ID for token_id in targets):
        wildcard_token_id = vocab_size
        emissions = np.concatenate(
            [
                emissions,
                wildcard_emission_column(emissions, blank_id=blank_id)[:, None],
            ],
            axis=1,
        )
        targets = [
            wildcard_token_id if token_id == WILDCARD_TARGET_ID else token_id
            for token_id in targets
        ]
    else:
        wildcard_token_id = -1
    if any(token_id < 0 or token_id >= emissions.shape[1] for token_id in targets):
        raise ValueError("target token is outside emission vocabulary")

    repeated_neighbors = sum(1 for left, right in zip(targets, targets[1:]) if left == right)
    minimum_frames = len(targets) + repeated_neighbors
    if frames < minimum_frames:
        raise AlignmentFailure(
            ERROR_WINDOW_MISMATCH,
            f"audio is too short for transcript frames={frames} required={minimum_frames}",
        )

    extended: list[int] = [blank_id]
    for token_id in targets:
        extended.extend([token_id, blank_id])
    state_count = len(extended)
    dp = np.full((frames, state_count), -np.inf, dtype=np.float64)
    back = np.full((frames, state_count), -1, dtype=np.int32)
    dp[0, 0] = emissions[0, blank_id]
    if state_count > 1:
        dp[0, 1] = emissions[0, extended[1]]

    for frame_idx in range(1, frames):
        for state_idx, token_id in enumerate(extended):
            candidates: list[tuple[float, int]] = [(dp[frame_idx - 1, state_idx], state_idx)]
            if state_idx > 0:
                candidates.append((dp[frame_idx - 1, state_idx - 1], state_idx - 1))
            if (
                state_idx > 1
                and token_id != blank_id
                and token_id != extended[state_idx - 2]
            ):
                candidates.append((dp[frame_idx - 1, state_idx - 2], state_idx - 2))
            best_score, best_prev = max(candidates, key=lambda item: item[0])
            if math.isfinite(best_score):
                dp[frame_idx, state_idx] = best_score + emissions[frame_idx, token_id]
                back[frame_idx, state_idx] = best_prev

    end_candidates = [state_count - 1]
    if state_count > 1:
        end_candidates.append(state_count - 2)
    end_state = max(end_candidates, key=lambda state_idx: dp[frames - 1, state_idx])
    total_score = float(dp[frames - 1, end_state])
    if not math.isfinite(total_score):
        raise AlignmentFailure(
            ERROR_WINDOW_MISMATCH,
            "no valid CTC path for the supplied audio and fragment",
        )

    states = np.full(frames, -1, dtype=np.int32)
    states[-1] = end_state
    for frame_idx in range(frames - 1, 0, -1):
        prev_state = int(back[frame_idx, states[frame_idx]])
        if prev_state < 0:
            raise AlignmentFailure(ERROR_INTERNAL, f"CTC backtrack failed at frame={frame_idx}")
        states[frame_idx - 1] = prev_state

    spans: list[TokenSpan] = []
    for target_index, token_id in enumerate(targets):
        token_state = 2 * target_index + 1
        assigned = np.flatnonzero(states == token_state)
        if assigned.size == 0:
            raise AlignmentFailure(
                ERROR_WINDOW_MISMATCH,
                f"target token has no aligned frames index={target_index}",
            )
        token_log_probs = emissions[assigned, token_id]
        blank_log_probs = emissions[assigned, blank_id]
        if token_id == wildcard_token_id:
            # A wildcard stands for an unknown, possibly multi-phoneme run of
            # hidden audio, so it has no single posterior peak to trim around.
            # Its whole occupancy is the timing evidence — that is what makes
            # the display word cover the masked letters.
            evidence_frames = assigned
        else:
            # Viterbi can hold a token state through unrelated frames merely to
            # preserve a path. Keep the connected posterior-supported region
            # around the strongest token-vs-blank peak as the timing evidence.
            log_odds = token_log_probs - blank_log_probs
            peak_offset = int(np.argmax(log_odds))
            relative_floor = float(token_log_probs[peak_offset]) + math.log(
                TOKEN_POSTERIOR_RELATIVE_FLOOR
            )
            evidence_mask = (token_log_probs >= relative_floor) & (
                log_odds >= math.log(TOKEN_TO_BLANK_ODDS_FLOOR)
            )
            evidence_mask[peak_offset] = True
            evidence_start = peak_offset
            while evidence_start > 0 and bool(evidence_mask[evidence_start - 1]):
                evidence_start -= 1
            evidence_end = peak_offset + 1
            while evidence_end < assigned.size and bool(evidence_mask[evidence_end]):
                evidence_end += 1
            evidence_frames = assigned[evidence_start:evidence_end]
        probabilities = np.exp(emissions[evidence_frames, token_id])
        spans.append(
            TokenSpan(
                target_index=target_index,
                token_id=token_id,
                start_frame=int(evidence_frames[0]),
                end_frame=int(evidence_frames[-1]) + 1,
                score=float(np.mean(probabilities)),
                path_start_frame=int(assigned[0]),
                path_end_frame=int(assigned[-1]) + 1,
                is_wildcard=bool(token_id == wildcard_token_id),
            )
        )
    return spans, total_score


def align_targets_in_window(
    log_probs: np.ndarray,
    target_ids: Sequence[int],
    *,
    blank_id: int,
    timeline: EmissionTimeline,
    clip_start_abs: float,
    clip_end_abs: float,
) -> tuple[list[TokenSpan], float, int, int]:
    """Align target tokens while keeping acoustic context outside the search."""
    search_start_frame, search_end_frame = timeline.constrained_frame_range(
        clip_start_abs=float(clip_start_abs),
        clip_end_abs=float(clip_end_abs),
    )
    constrained = np.asarray(log_probs)[search_start_frame:search_end_frame]
    spans, path_score = ctc_viterbi_align(
        constrained,
        target_ids,
        blank_id=blank_id,
    )
    return spans, path_score, search_start_frame, search_end_frame


def _choose_text_case(
    pronunciation_words: Sequence[str],
    vocab: dict[str, int],
    *,
    display_words: Sequence[str] | None = None,
) -> list[str]:
    candidates = (
        [word.lower() for word in pronunciation_words],
        [word.upper() for word in pronunciation_words],
        pronunciation_words,
    )

    def _missing(text: str) -> set[str]:
        # The wildcard is not a vocabulary grapheme: it is resolved into a
        # dedicated emission column at CTC time.
        return {
            character
            for character in set(text)
            if character != ALIGNMENT_WILDCARD and character not in vocab
        }

    for candidate in candidates:
        if not _missing("".join(candidate)):
            return candidate

    best_candidate = min(
        candidates,
        key=lambda candidate: len(_missing("".join(candidate))),
    )
    missing = sorted(_missing("".join(best_candidate)))
    error_display_words = display_words or pronunciation_words
    unsupported_words = [
        str(display_word)
        for display_word, pronunciation_word in zip(
            error_display_words,
            best_candidate,
        )
        if _missing(pronunciation_word)
    ]
    raise AlignmentFailure(
        ERROR_UNSUPPORTED_TEXT,
        "normalized pronunciation contains words unsupported by the Russian "
        "CTC vocabulary: "
        f"{unsupported_words!r}; unsupported characters: {missing!r}; "
        "fix the pronunciation normalizer or add an explicit override",
    )


def build_targets(
    *,
    display_words: Sequence[str],
    pronunciation_words: Sequence[str],
    tokenizer: Any,
) -> tuple[list[str], list[int], list[int]]:
    if len(display_words) != len(pronunciation_words):
        raise AlignmentFailure(
            ERROR_INTERNAL,
            "display and pronunciation word counts differ",
        )
    vocab = tokenizer.get_vocab()
    normalized_words = _choose_text_case(
        pronunciation_words,
        vocab,
        display_words=display_words,
    )
    delimiter_id = tokenizer.word_delimiter_token_id
    if delimiter_id is None:
        delimiter_token = getattr(tokenizer, "word_delimiter_token", None)
        if not delimiter_token or delimiter_token not in vocab:
            raise AlignmentFailure(ERROR_MODEL_UNAVAILABLE, "model has no word delimiter token")
        delimiter_id = int(vocab[delimiter_token])

    target_ids: list[int] = []
    token_word_indexes: list[int] = []
    unk_id = tokenizer.unk_token_id
    for word_index, word in enumerate(normalized_words):
        encoded: list[int] = []
        for unit_kind, unit_text in alignment_units(
            word,
            display_text=str(display_words[word_index]),
        ):
            if unit_kind == UNIT_WILDCARD:
                encoded.append(WILDCARD_TARGET_ID)
                continue
            unit_ids = [
                int(item)
                for item in tokenizer(unit_text, add_special_tokens=False).input_ids
            ]
            if not unit_ids or (unk_id is not None and int(unk_id) in unit_ids):
                raise AlignmentFailure(
                    ERROR_UNSUPPORTED_TEXT,
                    f"word contains unsupported model tokens index={word_index}",
                )
            encoded.extend(unit_ids)
        target_ids.extend(encoded)
        token_word_indexes.extend([word_index] * len(encoded))
        if word_index < len(normalized_words) - 1:
            target_ids.append(int(delimiter_id))
            token_word_indexes.append(-1)
    return normalized_words, target_ids, token_word_indexes


def aggregate_words(
    *,
    display_words: Sequence[str],
    normalized_words: Sequence[str],
    spans: Sequence[TokenSpan],
    token_word_indexes: Sequence[int],
    seconds_per_frame: float,
    analysis_start_abs: float,
) -> list[AlignedWord]:
    if len(spans) != len(token_word_indexes):
        raise ValueError(
            f"token span count mismatch: spans={len(spans)} mappings={len(token_word_indexes)}"
        )
    output: list[AlignedWord] = []
    for word_index, display_word in enumerate(display_words):
        word_spans = [
            span
            for span, mapped_word_index in zip(spans, token_word_indexes)
            if mapped_word_index == word_index
        ]
        if not word_spans:
            raise AlignmentFailure(
                ERROR_WINDOW_MISMATCH,
                f"word has no aligned spans index={word_index}",
            )
        # Wildcard spans define the word extent (they carry the masked audio)
        # but they carry no grapheme evidence, so they stay out of the score.
        scored_spans = [span for span in word_spans if not span.is_wildcard]
        if not scored_spans:
            raise AlignmentFailure(
                ERROR_INTERNAL,
                f"word has only wildcard spans index={word_index}",
            )
        start_frame = min(span.start_frame for span in word_spans)
        end_frame = max(span.end_frame for span in word_spans)
        local_start = float(start_frame) * seconds_per_frame
        local_end = float(end_frame) * seconds_per_frame
        output.append(
            AlignedWord(
                text=str(display_word),
                normalized_text=str(normalized_words[word_index]),
                t_start=float(analysis_start_abs) + local_start,
                t_end=float(analysis_start_abs) + local_end,
                local_start=local_start,
                local_end=local_end,
                confidence=float(np.mean([span.score for span in scored_spans])),
            )
        )
    return output


def generate_dynamic_window_bounds(
    *,
    clip_start_abs: float,
    clip_end_abs: float,
    max_adjust_sec: float,
    step_sec: float,
) -> list[tuple[float, float]]:
    """Generate a small deterministic set of boundary probes around a clip."""
    start = float(clip_start_abs)
    end = float(clip_end_abs)
    if start < 0.0 or end <= start:
        raise AlignmentFailure(
            ERROR_WINDOW_MISMATCH,
            f"invalid clip window {start:.3f}..{end:.3f}",
        )
    if max_adjust_sec <= 0.0 or step_sec <= 0.0 or step_sec > max_adjust_sec:
        raise AlignmentFailure(ERROR_INTERNAL, "invalid dynamic window search range")

    deltas: list[float] = []
    value = float(step_sec)
    while value < float(max_adjust_sec) - 1e-9:
        deltas.append(value)
        value += float(step_sec)
    deltas.append(float(max_adjust_sec))

    # Keep the Viterbi search bounded even when operators choose a very small
    # step. Coarse, midpoint and maximum probes retain coverage without turning
    # one alignment request into hundreds of Python DP passes.
    if len(deltas) > 3:
        midpoint = min(
            deltas[1:-1],
            key=lambda item: abs(item - (float(max_adjust_sec) / 2.0)),
        )
        deltas = [deltas[0], midpoint, deltas[-1]]

    variants: set[tuple[float, float]] = {(start, end)}
    for delta in deltas:
        variants.update(
            {
                (start - delta, end - delta),
                (start + delta, end + delta),
                (start - delta, end),
                (start, end + delta),
                (start + delta, end),
                (start, end - delta),
                (start - delta, end + delta),
                (start + delta, end - delta),
            }
        )

    normalized: set[tuple[float, float]] = set()
    for raw_start, raw_end in variants:
        candidate_start = max(0.0, float(raw_start))
        candidate_end = float(raw_end)
        if candidate_end <= candidate_start:
            continue
        normalized.add((round(candidate_start, 6), round(candidate_end, 6)))
    return sorted(
        normalized,
        key=lambda item: (
            abs(item[0] - start) + abs(item[1] - end),
            item[0],
            item[1],
        ),
    )


def _word_timing_deltas(
    left: WindowAlignmentCandidate,
    right: WindowAlignmentCandidate,
) -> list[float]:
    if len(left.words) != len(right.words):
        return []
    return [
        max(
            abs(float(left_word.t_start) - float(right_word.t_start)),
            abs(float(left_word.t_end) - float(right_word.t_end)),
        )
        for left_word, right_word in zip(left.words, right.words)
    ]


def _candidate_timings_are_stable(
    left: WindowAlignmentCandidate,
    right: WindowAlignmentCandidate,
    *,
    tolerance_sec: float,
    weak_boundary_tolerance_sec: float,
    max_interior_outlier_sec: float,
) -> bool:
    deltas = _word_timing_deltas(left, right)
    if not deltas:
        return False
    left_limit = (
        float(tolerance_sec)
        if left.left_confidence_supported or right.left_confidence_supported
        else float(weak_boundary_tolerance_sec)
    )
    right_limit = (
        float(tolerance_sec)
        if left.right_confidence_supported or right.right_confidence_supported
        else float(weak_boundary_tolerance_sec)
    )
    if deltas[0] > left_limit or deltas[-1] > right_limit:
        return False
    interior = deltas[1:-1]
    if not interior:
        return True
    allowed_outliers = int(math.floor(len(interior) * 0.10))
    unstable_count = sum(
        delta > float(tolerance_sec) for delta in interior
    )
    return (
        unstable_count <= allowed_outliers
        and max(interior) <= float(max_interior_outlier_sec)
    )


def _boundary_compression_is_actionable(
    *,
    left_duration_ratio: float,
    right_duration_ratio: float,
    minimum_duration_ratio: float,
    left_edge_supported: bool,
    right_edge_supported: bool,
    left_user_window_censored: bool,
    right_user_window_censored: bool,
) -> bool:
    """Return whether a short boundary word is evidence of window clipping.

    CTC emits narrow posterior spikes. A naturally short boundary word can
    therefore have a much smaller evidence extent per token than a longer word
    even when it is aligned correctly. Compression is actionable only when the
    same side is exposed to a search or authoritative user-window boundary.
    """
    threshold = float(minimum_duration_ratio)
    left_exposed = not bool(left_edge_supported) or bool(left_user_window_censored)
    right_exposed = not bool(right_edge_supported) or bool(right_user_window_censored)
    return (
        float(left_duration_ratio) < threshold and left_exposed
    ) or (
        float(right_duration_ratio) < threshold and right_exposed
    )


def _build_window_candidate(
    *,
    log_probs: np.ndarray,
    target_ids: Sequence[int],
    token_word_indexes: Sequence[int],
    display_words: Sequence[str],
    normalized_words: Sequence[str],
    blank_id: int,
    timeline: EmissionTimeline,
    search_start_abs: float,
    search_end_abs: float,
    clip_start_abs: float,
    clip_end_abs: float,
    config: DynamicWindowConfig,
    min_word_confidence: float,
) -> WindowAlignmentCandidate:
    token_spans, path_score, search_start_frame, search_end_frame = (
        align_targets_in_window(
            log_probs,
            target_ids,
            blank_id=blank_id,
            timeline=timeline,
            clip_start_abs=float(search_start_abs),
            clip_end_abs=float(search_end_abs),
        )
    )
    actual_start = timeline.frame_to_abs(search_start_frame)
    actual_end = timeline.frame_to_abs(search_end_frame)
    words = aggregate_words(
        display_words=display_words,
        normalized_words=normalized_words,
        spans=token_spans,
        token_word_indexes=token_word_indexes,
        seconds_per_frame=timeline.seconds_per_frame,
        analysis_start_abs=actual_start,
    )
    confidences = np.asarray(
        [float(word.confidence) for word in words],
        dtype=np.float64,
    )
    left_boundary_confidence = float(confidences[0])
    right_boundary_confidence = float(confidences[-1])
    boundary_confidence = min(
        left_boundary_confidence,
        right_boundary_confidence,
    )
    left_confidence_supported = (
        left_boundary_confidence >= float(min_word_confidence)
    )
    right_confidence_supported = (
        right_boundary_confidence >= float(min_word_confidence)
    )
    left_clearance = max(0.0, float(words[0].t_start) - actual_start)
    right_clearance = max(0.0, actual_end - float(words[-1].t_end))
    frame_tolerance = timeline.seconds_per_frame * 1.01
    left_context_limited = actual_start <= timeline.analysis_start_abs + frame_tolerance
    right_context_limited = actual_end >= timeline.analysis_end_abs - frame_tolerance
    censor_tolerance = max(float(config.min_edge_clearance_sec), frame_tolerance)
    left_user_window_censored = (
        float(words[0].t_start) <= float(clip_start_abs) + censor_tolerance
    )
    right_user_window_censored = (
        float(words[-1].t_end) >= float(clip_end_abs) - censor_tolerance
    )

    token_counts = [
        max(1, sum(1 for item in token_word_indexes if int(item) == word_index))
        for word_index in range(len(words))
    ]
    duration_per_token = np.asarray(
        [
            (float(word.t_end) - float(word.t_start)) / float(token_count)
            for word, token_count in zip(words, token_counts)
        ],
        dtype=np.float64,
    )
    if len(duration_per_token) > 2:
        duration_reference = float(np.median(duration_per_token[1:-1]))
    else:
        duration_reference = float(np.median(duration_per_token))
    duration_reference = max(duration_reference, timeline.seconds_per_frame)
    left_boundary_duration_ratio = min(
        1.0,
        float(duration_per_token[0]) / duration_reference,
    )
    right_boundary_duration_ratio = min(
        1.0,
        float(duration_per_token[-1]) / duration_reference,
    )
    boundary_duration_ratio = min(
        left_boundary_duration_ratio,
        right_boundary_duration_ratio,
    )

    evidence_frame_count = sum(
        int(span.end_frame) - int(span.start_frame) for span in token_spans
    )
    path_frame_count = sum(
        int(span.path_end_frame) - int(span.path_start_frame) for span in token_spans
    )
    path_to_evidence_ratio = float(path_frame_count) / float(
        max(1, evidence_frame_count)
    )
    search_frame_count = max(1, int(search_end_frame) - int(search_start_frame))
    path_mean_probability = math.exp(
        max(-50.0, min(0.0, float(path_score) / float(search_frame_count)))
    )

    edge_floor = float(config.min_edge_clearance_sec)
    left_edge_score = (
        1.0
        if left_context_limited or edge_floor == 0.0
        else min(1.0, left_clearance / edge_floor)
    )
    right_edge_score = (
        1.0
        if right_context_limited or edge_floor == 0.0
        else min(1.0, right_clearance / edge_floor)
    )
    edge_score = min(left_edge_score, right_edge_score)
    max_total_adjustment = max(1e-9, 2.0 * float(config.max_adjust_sec))
    adjustment = abs(actual_start - float(clip_start_abs)) + abs(
        actual_end - float(clip_end_abs)
    )
    adjustment_score = 1.0 - min(1.0, adjustment / max_total_adjustment)
    confidence_p10 = float(np.quantile(confidences, 0.10))
    quality_score = (
        0.30 * float(np.mean(confidences))
        + 0.20 * confidence_p10
        + 0.20 * boundary_confidence
        + 0.10 * path_mean_probability
        + 0.10 * edge_score
        + 0.07 * boundary_duration_ratio
        + 0.03 * adjustment_score
    )

    rejection_reasons: list[str] = []
    if any(
        float(word.t_start) < float(clip_start_abs) - frame_tolerance
        or float(word.t_end) > float(clip_end_abs) + frame_tolerance
        for word in words
    ):
        rejection_reasons.append("outside_user_window")
    # Interior confidence is quality telemetry. Boundary confidence is tracked
    # independently per side so a stable consensus can combine evidence from
    # different probes without allowing a weak side to pass unnoticed.
    if not left_confidence_supported:
        rejection_reasons.append("low_left_boundary_word_confidence")
    if not right_confidence_supported:
        rejection_reasons.append("low_right_boundary_word_confidence")
    if left_edge_score < 1.0 or right_edge_score < 1.0:
        rejection_reasons.append("insufficient_edge_clearance")
    if _boundary_compression_is_actionable(
        left_duration_ratio=left_boundary_duration_ratio,
        right_duration_ratio=right_boundary_duration_ratio,
        minimum_duration_ratio=float(config.min_boundary_duration_ratio),
        left_edge_supported=left_edge_score >= 1.0 - 1e-9,
        right_edge_supported=right_edge_score >= 1.0 - 1e-9,
        left_user_window_censored=left_user_window_censored,
        right_user_window_censored=right_user_window_censored,
    ):
        rejection_reasons.append("boundary_word_compression")

    return WindowAlignmentCandidate(
        search_start_abs=float(actual_start),
        search_end_abs=float(actual_end),
        search_start_frame=int(search_start_frame),
        search_end_frame=int(search_end_frame),
        token_spans=tuple(token_spans),
        words=tuple(words),
        path_log_score=float(path_score),
        mean_word_confidence=float(np.mean(confidences)),
        min_word_confidence=float(np.min(confidences)),
        boundary_word_confidence=float(boundary_confidence),
        left_boundary_word_confidence=float(left_boundary_confidence),
        right_boundary_word_confidence=float(right_boundary_confidence),
        left_confidence_supported=bool(left_confidence_supported),
        right_confidence_supported=bool(right_confidence_supported),
        path_mean_probability=float(path_mean_probability),
        left_edge_clearance_sec=float(left_clearance),
        right_edge_clearance_sec=float(right_clearance),
        left_edge_supported=bool(left_edge_score >= 1.0 - 1e-9),
        right_edge_supported=bool(right_edge_score >= 1.0 - 1e-9),
        left_user_window_censored=bool(left_user_window_censored),
        right_user_window_censored=bool(right_user_window_censored),
        boundary_duration_ratio=float(boundary_duration_ratio),
        path_to_evidence_ratio=float(path_to_evidence_ratio),
        adjustment_sec=float(adjustment),
        quality_score=float(quality_score),
        rejection_reasons=tuple(rejection_reasons),
    )


def select_dynamic_alignment_window(
    *,
    log_probs: np.ndarray,
    target_ids: Sequence[int],
    token_word_indexes: Sequence[int],
    display_words: Sequence[str],
    normalized_words: Sequence[str],
    blank_id: int,
    timeline: EmissionTimeline,
    clip_start_abs: float,
    clip_end_abs: float,
    config: DynamicWindowConfig,
    min_word_confidence: float,
) -> DynamicWindowSelection:
    config.validate()
    requested_bounds = generate_dynamic_window_bounds(
        clip_start_abs=float(clip_start_abs),
        clip_end_abs=float(clip_end_abs),
        max_adjust_sec=float(config.max_adjust_sec),
        step_sec=float(config.step_sec),
    )
    candidates: list[WindowAlignmentCandidate] = []
    failed_candidates: list[dict[str, Any]] = []
    seen_frame_ranges: set[tuple[int, int]] = set()
    for requested_start, requested_end in requested_bounds:
        bounded_start = max(float(timeline.analysis_start_abs), requested_start)
        bounded_end = min(float(timeline.analysis_end_abs), requested_end)
        if bounded_end <= bounded_start:
            continue
        try:
            frame_range = timeline.constrained_frame_range(
                clip_start_abs=bounded_start,
                clip_end_abs=bounded_end,
            )
            if frame_range in seen_frame_ranges:
                continue
            seen_frame_ranges.add(frame_range)
            candidates.append(
                _build_window_candidate(
                    log_probs=log_probs,
                    target_ids=target_ids,
                    token_word_indexes=token_word_indexes,
                    display_words=display_words,
                    normalized_words=normalized_words,
                    blank_id=blank_id,
                    timeline=timeline,
                    search_start_abs=bounded_start,
                    search_end_abs=bounded_end,
                    clip_start_abs=float(clip_start_abs),
                    clip_end_abs=float(clip_end_abs),
                    config=config,
                    min_word_confidence=float(min_word_confidence),
                )
            )
        except AlignmentFailure as exc:
            failed_candidates.append(
                {
                    "search_start_abs": float(bounded_start),
                    "search_end_abs": float(bounded_end),
                    "error_code": exc.code,
                }
            )

    reason_counts: dict[str, int] = {}
    for candidate in candidates:
        for reason in candidate.rejection_reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    frame_tolerance = timeline.seconds_per_frame * 1.01
    left_confident_outside_candidates = [
        candidate
        for candidate in candidates
        if candidate.left_confidence_supported
        and float(candidate.words[0].t_start)
        < float(clip_start_abs) - frame_tolerance
    ]
    right_confident_outside_candidates = [
        candidate
        for candidate in candidates
        if candidate.right_confidence_supported
        and float(candidate.words[-1].t_end)
        > float(clip_end_abs) + frame_tolerance
    ]

    # Window containment and non-compressed boundary words are hard validity
    # rules. Edge clearance and per-side acoustic confidence are independent
    # evidence signals: stable probes may prove them with different candidates.
    # A boundary touching the authoritative user window is a censored
    # observation, not automatic evidence that the requested fragment is wrong.
    evidence_reasons = {
        "insufficient_edge_clearance",
        "low_left_boundary_word_confidence",
        "low_right_boundary_word_confidence",
    }
    hard_valid_candidates = [
        candidate
        for candidate in candidates
        if not set(candidate.rejection_reasons).difference(evidence_reasons)
    ]
    fully_supported_candidates = [
        candidate
        for candidate in hard_valid_candidates
        if candidate.left_edge_supported
        and candidate.right_edge_supported
        and candidate.left_confidence_supported
        and candidate.right_confidence_supported
    ]

    def _failure_details() -> dict[str, Any]:
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                len(
                    set(candidate.rejection_reasons).difference(evidence_reasons)
                ),
                -candidate.quality_score,
                candidate.adjustment_sec,
            ),
        )
        return {
            "candidate_count": len(candidates),
            "failed_candidate_count": len(failed_candidates),
            "rejection_counts": dict(reason_counts),
            "hard_valid_candidate_count": len(hard_valid_candidates),
            "fully_supported_candidate_count": len(fully_supported_candidates),
            "left_confident_outside_candidate_count": len(
                left_confident_outside_candidates
            ),
            "right_confident_outside_candidate_count": len(
                right_confident_outside_candidates
            ),
            "left_edge_supported_candidate_count": sum(
                candidate.left_edge_supported
                for candidate in hard_valid_candidates
            ),
            "right_edge_supported_candidate_count": sum(
                candidate.right_edge_supported
                for candidate in hard_valid_candidates
            ),
            "left_confidence_supported_candidate_count": sum(
                candidate.left_confidence_supported
                for candidate in hard_valid_candidates
            ),
            "right_confidence_supported_candidate_count": sum(
                candidate.right_confidence_supported
                for candidate in hard_valid_candidates
            ),
            "left_user_window_censored_candidate_count": sum(
                candidate.left_user_window_censored
                and candidate.left_confidence_supported
                for candidate in hard_valid_candidates
            ),
            "right_user_window_censored_candidate_count": sum(
                candidate.right_user_window_censored
                and candidate.right_confidence_supported
                for candidate in hard_valid_candidates
            ),
            "top_candidates": [candidate.summary() for candidate in ranked[:12]],
            "failed_candidates": failed_candidates[:12],
        }

    if not hard_valid_candidates:
        raise AlignmentFailure(
            ERROR_WINDOW_MISMATCH,
            "dynamic window search found no hard-valid alignment "
            f"candidates={len(candidates)} failed={len(failed_candidates)} "
            f"rejections={reason_counts}",
            details=_failure_details(),
        )

    hard_valid_candidates.sort(
        key=lambda candidate: (
            -candidate.quality_score,
            candidate.adjustment_sec,
            candidate.search_start_abs,
            candidate.search_end_abs,
        )
    )
    best_score = max(
        float(candidate.quality_score) for candidate in hard_valid_candidates
    )
    score_pool = [
        candidate
        for candidate in hard_valid_candidates
        if candidate.quality_score >= best_score - float(config.score_tolerance)
    ]

    stability_tolerance = float(config.stability_tolerance_sec)
    weak_boundary_tolerance = max(
        stability_tolerance,
        float(config.step_sec) + timeline.seconds_per_frame * 1.01,
    )
    max_interior_outlier = max(
        3.0 * stability_tolerance,
        weak_boundary_tolerance,
    )
    clusters: list[tuple[WindowAlignmentCandidate, list[WindowAlignmentCandidate]]] = []
    for anchor in score_pool:
        members = [
            candidate
            for candidate in score_pool
            if _candidate_timings_are_stable(
                anchor,
                candidate,
                tolerance_sec=stability_tolerance,
                weak_boundary_tolerance_sec=weak_boundary_tolerance,
                max_interior_outlier_sec=max_interior_outlier,
            )
        ]
        clusters.append((anchor, members))

    def _cluster_boundary_evidence(
        members: Sequence[WindowAlignmentCandidate],
    ) -> tuple[bool, bool, bool, bool, bool, bool]:
        left_edge = any(candidate.left_edge_supported for candidate in members)
        right_edge = any(candidate.right_edge_supported for candidate in members)
        left_confidence = any(
            candidate.left_confidence_supported for candidate in members
        )
        right_confidence = any(
            candidate.right_confidence_supported for candidate in members
        )
        left_censored = any(
            candidate.left_user_window_censored
            and candidate.left_confidence_supported
            for candidate in members
        )
        right_censored = any(
            candidate.right_user_window_censored
            and candidate.right_confidence_supported
            for candidate in members
        )
        return (
            left_edge,
            right_edge,
            left_confidence,
            right_confidence,
            left_censored,
            right_censored,
        )

    minimum_consensus = int(config.min_consensus_candidates)
    stable_clusters = [
        item for item in clusters if len(item[1]) >= minimum_consensus
    ]
    largest_consensus = max((len(item[1]) for item in clusters), default=0)

    boundary_supported_clusters = []
    for item in stable_clusters:
        (
            left_edge,
            right_edge,
            left_confidence,
            right_confidence,
            left_censored,
            right_censored,
        ) = _cluster_boundary_evidence(item[1])
        if (
            left_confidence
            and right_confidence
            and (left_edge or left_censored)
            and (right_edge or right_censored)
        ):
            boundary_supported_clusters.append(item)

    # Stable timings across independently shifted windows are acoustic evidence
    # in their own right. They may replace a weak boundary posterior only when
    # expanded probes do not confidently place that same boundary outside the
    # authoritative user window.
    timing_supported_clusters = []
    for item in stable_clusters:
        (
            _,
            _,
            left_confidence,
            right_confidence,
            _,
            _,
        ) = _cluster_boundary_evidence(item[1])
        if (
            (
                left_confidence
                or len(left_confident_outside_candidates) < minimum_consensus
            )
            and (
                right_confidence
                or len(right_confident_outside_candidates) < minimum_consensus
            )
        ):
            timing_supported_clusters.append(item)
    selection_degraded = False
    selection_reason = "strict_boundary_consensus"
    selection_warnings: list[str] = []
    timing_consensus_used = not boundary_supported_clusters
    selection_clusters = boundary_supported_clusters or timing_supported_clusters
    if selection_clusters:
        anchor, consensus = max(
            selection_clusters,
            key=lambda item: (
                len(item[1]),
                float(np.mean([candidate.quality_score for candidate in item[1]])),
                -float(np.mean([candidate.adjustment_sec for candidate in item[1]])),
                item[0].quality_score,
            ),
        )
        if not boundary_supported_clusters:
            selection_reason = "stable_timing_consensus"
    elif stable_clusters:
        # Boundary confidence, edge clearance and expanded-window probes are
        # quality evidence, not validity constraints. A different probe must
        # not veto an otherwise contained, non-compressed stable cluster.
        selection_degraded = True
        selection_reason = "stable_cluster_with_boundary_counterevidence"
        selection_warnings.append("boundary_counterevidence_outside_user_window")
        anchor, consensus = max(
            stable_clusters,
            key=lambda item: (
                len(item[1]),
                float(np.mean([candidate.quality_score for candidate in item[1]])),
                -float(np.mean([candidate.adjustment_sec for candidate in item[1]])),
                item[0].quality_score,
            ),
        )
    else:
        # The score pool contains only hard-valid candidates. When small CTC
        # timing jitter prevents the requested strict cluster size, use its
        # robust medoid deterministically and expose the reduced confidence.
        selection_degraded = True
        selection_reason = "hard_valid_medoid_without_strict_consensus"
        selection_warnings.append("insufficient_timing_consensus")
        consensus = list(score_pool)
        anchor = score_pool[0]
    (
        consensus_left_edge,
        consensus_right_edge,
        consensus_left_confidence,
        consensus_right_confidence,
        consensus_left_censored,
        consensus_right_censored,
    ) = _cluster_boundary_evidence(consensus)
    boundary_evidence_warnings: list[str] = []
    if not consensus_left_confidence:
        boundary_evidence_warnings.append("low_left_boundary_word_confidence")
    if not consensus_right_confidence:
        boundary_evidence_warnings.append("low_right_boundary_word_confidence")
    if not (consensus_left_edge or consensus_left_censored):
        boundary_evidence_warnings.append("missing_left_edge_clearance")
    if not (consensus_right_edge or consensus_right_censored):
        boundary_evidence_warnings.append("missing_right_edge_clearance")

    median_starts = np.median(
        np.asarray(
            [[float(word.t_start) for word in candidate.words] for candidate in consensus]
        ),
        axis=0,
    )
    median_ends = np.median(
        np.asarray(
            [[float(word.t_end) for word in candidate.words] for candidate in consensus]
        ),
        axis=0,
    )

    def _distance_to_median(candidate: WindowAlignmentCandidate) -> float:
        deltas = [
            max(
                abs(float(word.t_start) - float(median_starts[index])),
                abs(float(word.t_end) - float(median_ends[index])),
            )
            for index, word in enumerate(candidate.words)
        ]
        return float(np.mean(deltas))

    selected = min(
        consensus,
        key=lambda candidate: (
            _distance_to_median(candidate),
            -int(candidate.left_confidence_supported)
            - int(candidate.right_confidence_supported),
            -int(candidate.left_edge_supported)
            - int(candidate.right_edge_supported),
            -candidate.quality_score,
            candidate.adjustment_sec,
            candidate.search_start_abs,
            candidate.search_end_abs,
        ),
    )
    if selection_degraded:
        anchor = selected
    word_stability = [
        {
            "word_index": index,
            "median_start_abs": float(median_starts[index]),
            "median_end_abs": float(median_ends[index]),
            "max_deviation_sec": max(
                max(
                    abs(float(candidate.words[index].t_start) - float(median_starts[index])),
                    abs(float(candidate.words[index].t_end) - float(median_ends[index])),
                )
                for candidate in consensus
            ),
        }
        for index in range(len(selected.words))
    ]
    per_word_deviations = [
        float(item["max_deviation_sec"]) for item in word_stability
    ]
    max_timing_deviation = max(per_word_deviations)
    left_boundary_deviation = per_word_deviations[0]
    right_boundary_deviation = per_word_deviations[-1]
    left_boundary_limit = (
        stability_tolerance
        if consensus_left_confidence
        else weak_boundary_tolerance
    )
    right_boundary_limit = (
        stability_tolerance
        if consensus_right_confidence
        else weak_boundary_tolerance
    )
    interior_deviations = per_word_deviations[1:-1]
    allowed_interior_outliers = int(
        math.floor(len(interior_deviations) * 0.10)
    )
    unstable_interior_word_count = sum(
        deviation > stability_tolerance
        for deviation in interior_deviations
    )
    max_interior_deviation = max(interior_deviations, default=0.0)
    stability_failures: list[str] = []
    if left_boundary_deviation > left_boundary_limit + 1e-9:
        stability_failures.append("left_boundary")
    if right_boundary_deviation > right_boundary_limit + 1e-9:
        stability_failures.append("right_boundary")
    if unstable_interior_word_count > allowed_interior_outliers:
        stability_failures.append("too_many_interior_outliers")
    if max_interior_deviation > max_interior_outlier + 1e-9:
        stability_failures.append("interior_outlier_too_large")
    if stability_failures:
        selection_degraded = True
        if selection_reason == "strict_boundary_consensus":
            selection_reason = "hard_valid_medoid_with_collective_instability"
        selection_warnings.extend(
            f"robust_consensus_{failure}" for failure in stability_failures
        )

    ranked_summaries = [
        candidate.summary()
        for candidate in sorted(
            candidates,
            key=lambda candidate: (
                bool(candidate.rejection_reasons),
                -candidate.quality_score,
                candidate.adjustment_sec,
            ),
        )[:12]
    ]

    failure_code_counts: dict[str, int] = {}
    for failure in failed_candidates:
        code = str(failure["error_code"])
        failure_code_counts[code] = failure_code_counts.get(code, 0) + 1
    diagnostics = {
        "mode": "single_inference_multi_window_consensus",
        "boundary_duration_gate_mode": "compression_requires_boundary_exposure",
        "requested_start_abs": float(clip_start_abs),
        "requested_end_abs": float(clip_end_abs),
        "requested_candidate_count": len(requested_bounds),
        "candidate_count": len(candidates),
        "failed_candidate_count": len(failed_candidates),
        "failed_candidate_code_counts": failure_code_counts,
        "eligible_candidate_count": len(fully_supported_candidates),
        "hard_valid_candidate_count": len(hard_valid_candidates),
        "stability_candidate_count": len(hard_valid_candidates),
        "left_confident_outside_candidate_count": len(
            left_confident_outside_candidates
        ),
        "right_confident_outside_candidate_count": len(
            right_confident_outside_candidates
        ),
        "evidence_limited_candidate_count": sum(
            bool(candidate.rejection_reasons)
            for candidate in hard_valid_candidates
        ),
        "edge_probe_candidate_count": sum(
            "insufficient_edge_clearance" in candidate.rejection_reasons
            for candidate in hard_valid_candidates
        ),
        "left_edge_supported_candidate_count": sum(
            candidate.left_edge_supported for candidate in hard_valid_candidates
        ),
        "right_edge_supported_candidate_count": sum(
            candidate.right_edge_supported for candidate in hard_valid_candidates
        ),
        "left_confidence_supported_candidate_count": sum(
            candidate.left_confidence_supported
            for candidate in hard_valid_candidates
        ),
        "right_confidence_supported_candidate_count": sum(
            candidate.right_confidence_supported
            for candidate in hard_valid_candidates
        ),
        "left_user_window_censored_candidate_count": sum(
            candidate.left_user_window_censored
            and candidate.left_confidence_supported
            for candidate in hard_valid_candidates
        ),
        "right_user_window_censored_candidate_count": sum(
            candidate.right_user_window_censored
            and candidate.right_confidence_supported
            for candidate in hard_valid_candidates
        ),
        "boundary_evidence_mode": (
            "degraded_hard_valid_medoid"
            if selection_degraded
            else (
                "timing_consensus"
                if timing_consensus_used
                else (
                    "single_candidate"
                    if any(
                        candidate.left_edge_supported
                        and candidate.right_edge_supported
                        and candidate.left_confidence_supported
                        and candidate.right_confidence_supported
                        for candidate in consensus
                    )
                    else (
                        "split_consensus"
                        if any(candidate.left_edge_supported for candidate in consensus)
                        and any(candidate.right_edge_supported for candidate in consensus)
                        else "user_window_censored"
                    )
                )
            )
        ),
        "boundary_evidence_warnings": list(
            dict.fromkeys(boundary_evidence_warnings + selection_warnings)
        ),
        "degraded_confidence": bool(selection_degraded),
        "selection_reason": selection_reason,
        "rejection_counts": reason_counts,
        "score_pool_count": len(score_pool),
        "stable_cluster_count": len(stable_clusters),
        "largest_consensus_candidate_count": int(largest_consensus),
        "consensus_candidate_count": len(consensus),
        "consensus_anchor_start_abs": float(anchor.search_start_abs),
        "consensus_anchor_end_abs": float(anchor.search_end_abs),
        "max_timing_deviation_sec": float(max_timing_deviation),
        "left_boundary_deviation_sec": float(left_boundary_deviation),
        "right_boundary_deviation_sec": float(right_boundary_deviation),
        "max_interior_deviation_sec": float(max_interior_deviation),
        "unstable_interior_word_count": int(unstable_interior_word_count),
        "allowed_interior_outlier_count": int(allowed_interior_outliers),
        "word_stability": word_stability,
        "selected": selected.summary(),
        "top_candidates": ranked_summaries,
        "failed_candidates": failed_candidates[:12],
        "policy": {
            "max_adjust_sec": float(config.max_adjust_sec),
            "step_sec": float(config.step_sec),
            "min_edge_clearance_sec": float(config.min_edge_clearance_sec),
            "stability_tolerance_sec": float(config.stability_tolerance_sec),
            "weak_boundary_tolerance_sec": float(weak_boundary_tolerance),
            "max_interior_outlier_sec": float(max_interior_outlier),
            "max_interior_outlier_ratio": 0.10,
            "min_consensus_candidates": int(config.min_consensus_candidates),
            "score_tolerance": float(config.score_tolerance),
            "min_boundary_duration_ratio": float(
                config.min_boundary_duration_ratio
            ),
            "min_boundary_word_confidence": float(min_word_confidence),
        },
    }
    return DynamicWindowSelection(selected=selected, diagnostics=diagnostics)



def _validate_aligned_words(
    *,
    words: Sequence[AlignedWord],
    clip_start_abs: float,
    clip_end_abs: float,
    min_word_confidence: float,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    previous_start = -1.0
    previous_end = -1.0
    for index, word in enumerate(words):
        if word.t_end <= word.t_start:
            raise AlignmentFailure(ERROR_INTERNAL, f"non-positive word duration index={index}")
        if word.t_start < previous_start or word.t_end < previous_end:
            raise AlignmentFailure(ERROR_INTERNAL, f"non-monotonic word timing index={index}")
        if word.t_end < clip_start_abs or word.t_start > clip_end_abs:
            raise AlignmentFailure(
                ERROR_WINDOW_MISMATCH,
                f"aligned word is outside user window index={index}",
            )
        if word.confidence < min_word_confidence:
            warnings.append(
                {
                    "code": "low_confidence",
                    "word_index": index,
                    "score": round(float(word.confidence), 6),
                }
            )
        previous_start = word.t_start
        previous_end = word.t_end
    return warnings


def _derive_pause_spans(words: Sequence[AlignedWord], *, min_gap_sec: float) -> list[dict[str, Any]]:
    pauses: list[dict[str, Any]] = []
    for left, right in zip(words, words[1:]):
        if float(right.t_start) - float(left.t_end) >= float(min_gap_sec):
            pauses.append(
                {
                    "text": "[pause]",
                    "t_start": float(left.t_end),
                    "t_end": float(right.t_start),
                }
            )
    return pauses


def _build_stage1_asr(
    *,
    words: Sequence[AlignedWord],
    target_fragment: str,
    clip_start_abs: float,
    clip_end_abs: float,
    pause_min_gap_sec: float,
) -> Stage1AsrPayload:
    transcript_words = [
        {
            "text": word.text,
            "t_start": float(word.t_start),
            "t_end": float(word.t_end),
        }
        for word in words
    ]
    pause_spans = _derive_pause_spans(words, min_gap_sec=pause_min_gap_sec)
    window_duration = float(clip_end_abs) - float(clip_start_abs)
    fragment_relation = (
        "inside_13_18" if window_duration <= 18.0 else "inside_13_30"
    )
    return Stage1AsrPayload.model_validate(
        {
            "transcript_words": transcript_words,
            "pause_spans": pause_spans,
            "srt_items": [],
            "selected_fragment": {
                "audio": {
                    "clip_start_abs": float(clip_start_abs),
                    "clip_end_abs": float(clip_end_abs),
                },
                "transcript_words": transcript_words,
                "pause_spans": pause_spans,
                "srt_items": [],
                "fragment_analytics": {
                    "target_fragment": str(target_fragment),
                    "working_fragment": str(target_fragment),
                    "working_start_abs": float(clip_start_abs),
                    "working_end_abs": float(clip_end_abs),
                    "working_start_text": "user_clip_start",
                    "working_end_text": "user_clip_end",
                    "relation_to_target": fragment_relation,
                    "chosen_action": "none",
                    "rationale": "local_ctc_user_window_is_source_of_truth",
                },
            },
        }
    )


def align_target_fragment(
    *,
    audio_path: Path,
    target_fragment: str,
    clip_start_abs: float,
    clip_end_abs: float,
    processor: Any,
    model: Any,
    torch_module: Any,
    vocal_separator: Any,
    pronunciation_normalizer: Any,
    model_revision: str,
    ffmpeg_bin: str = "ffmpeg",
    ffmpeg_timeout_s: float = 120.0,
    padding_left_sec: float = 0.5,
    padding_right_sec: float = 0.5,
    min_word_confidence: float = 0.05,
    pause_min_gap_sec: float = 0.35,
    dynamic_window_max_adjust_sec: float = 1.0,
    dynamic_window_step_sec: float = 0.25,
    dynamic_window_min_edge_clearance_sec: float = 0.12,
    dynamic_window_stability_tolerance_sec: float = 0.12,
    dynamic_window_min_consensus_candidates: int = 3,
    dynamic_window_score_tolerance: float = 0.12,
    dynamic_window_min_boundary_duration_ratio: float = 0.15,
) -> AlignmentResult:
    display_words = reference_words(target_fragment)
    dynamic_window_config = DynamicWindowConfig(
        max_adjust_sec=float(dynamic_window_max_adjust_sec),
        step_sec=float(dynamic_window_step_sec),
        min_edge_clearance_sec=float(dynamic_window_min_edge_clearance_sec),
        stability_tolerance_sec=float(dynamic_window_stability_tolerance_sec),
        min_consensus_candidates=int(dynamic_window_min_consensus_candidates),
        score_tolerance=float(dynamic_window_score_tolerance),
        min_boundary_duration_ratio=float(
            dynamic_window_min_boundary_duration_ratio
        ),
    )
    dynamic_window_config.validate()
    with tempfile.TemporaryDirectory(prefix="blast_alignment_") as temp_dir_raw:
        crop_path = Path(temp_dir_raw) / "analysis_crop.wav"
        analysis_start, requested_analysis_end = extract_analysis_crop(
            ffmpeg_bin=ffmpeg_bin,
            audio_path=Path(audio_path),
            output_path=crop_path,
            clip_start_abs=float(clip_start_abs),
            clip_end_abs=float(clip_end_abs),
            padding_left_sec=(
                float(padding_left_sec) + dynamic_window_config.max_adjust_sec
            ),
            padding_right_sec=(
                float(padding_right_sec) + dynamic_window_config.max_adjust_sec
            ),
            sample_rate=int(vocal_separator.input_sample_rate),
            channels=int(vocal_separator.input_channels),
            timeout_s=float(ffmpeg_timeout_s),
        )
        separation = vocal_separator.separate_vocals(crop_path)
        waveform = separation.waveform
        sample_rate = int(separation.sample_rate)
        if sample_rate != SAMPLE_RATE or getattr(waveform, "ndim", 0) != 1:
            raise AlignmentFailure(
                ERROR_INTERNAL,
                "source separator produced an invalid analysis waveform",
            )
        if int(waveform.size) < SAMPLE_RATE // 10:
            raise AlignmentFailure(ERROR_WINDOW_MISMATCH, "analysis crop is too short")

        pronunciation_words = pronunciation_normalizer.normalize_words(display_words)
        normalized_words, target_ids, token_word_indexes = build_targets(
            display_words=display_words,
            pronunciation_words=[
                word.alignment_text for word in pronunciation_words
            ],
            tokenizer=processor.tokenizer,
        )
        model_inputs = processor(
            waveform,
            sampling_rate=SAMPLE_RATE,
            return_tensors="pt",
        )
        input_values = model_inputs.input_values
        attention_mask = getattr(model_inputs, "attention_mask", None)
        with torch_module.inference_mode():
            logits = model(input_values, attention_mask=attention_mask).logits[0]
            log_probs = torch_module.log_softmax(logits, dim=-1).detach().cpu().numpy()

    blank_id = int(model.config.pad_token_id)
    inputs_to_logits_ratio = int(
        getattr(model.config, "inputs_to_logits_ratio", 0) or 0
    )
    if inputs_to_logits_ratio <= 0:
        raise AlignmentFailure(
            ERROR_MODEL_UNAVAILABLE,
            "model config has no valid inputs_to_logits_ratio",
        )
    timeline = EmissionTimeline(
        analysis_start_abs=float(analysis_start),
        sample_rate=SAMPLE_RATE,
        input_samples=int(waveform.size),
        emission_frames=int(log_probs.shape[0]),
        inputs_to_logits_ratio=inputs_to_logits_ratio,
    )
    frame_tolerance = timeline.seconds_per_frame
    if float(clip_end_abs) > timeline.analysis_end_abs + frame_tolerance:
        raise AlignmentFailure(
            ERROR_WINDOW_MISMATCH,
            "user window exceeds available decoded audio "
            f"clip_end={float(clip_end_abs):.6f} "
            f"audio_end={timeline.analysis_end_abs:.6f}",
        )
    selection = select_dynamic_alignment_window(
        log_probs=log_probs,
        target_ids=target_ids,
        token_word_indexes=token_word_indexes,
        display_words=display_words,
        normalized_words=normalized_words,
        blank_id=blank_id,
        timeline=timeline,
        clip_start_abs=float(clip_start_abs),
        clip_end_abs=float(clip_end_abs),
        config=dynamic_window_config,
        min_word_confidence=float(min_word_confidence),
    )
    selected_candidate = selection.selected
    token_spans = list(selected_candidate.token_spans)
    path_score = float(selected_candidate.path_log_score)
    search_start_frame = int(selected_candidate.search_start_frame)
    search_end_frame = int(selected_candidate.search_end_frame)
    seconds_per_frame = timeline.seconds_per_frame
    alignment_origin_abs = float(selected_candidate.search_start_abs)
    aligned_words = list(selected_candidate.words)
    warnings = _validate_aligned_words(
        words=aligned_words,
        clip_start_abs=float(clip_start_abs),
        clip_end_abs=float(clip_end_abs),
        min_word_confidence=float(min_word_confidence),
    )
    stage1_asr = _build_stage1_asr(
        words=aligned_words,
        target_fragment=target_fragment,
        clip_start_abs=float(clip_start_abs),
        clip_end_abs=float(clip_end_abs),
        pause_min_gap_sec=float(pause_min_gap_sec),
    )
    confidences = [float(word.confidence) for word in aligned_words]
    evidence_frame_count = sum(
        int(span.end_frame) - int(span.start_frame) for span in token_spans
    )
    path_frame_count = sum(
        int(span.path_end_frame) - int(span.path_start_frame) for span in token_spans
    )
    path_to_evidence_ratio = float(path_frame_count) / float(evidence_frame_count)
    return AlignmentResult(
        stage1_asr=stage1_asr,
        diagnostics={
            "word_count": len(aligned_words),
            "mean_word_confidence": float(np.mean(confidences)),
            "min_word_confidence": float(min(confidences)),
            "warnings": warnings,
            "dynamic_window": selection.diagnostics,
            "redaction": {
                "markers": sorted(REDACTION_MARKERS),
                "wildcard_non_blank_weight": float(WILDCARD_NON_BLANK_WEIGHT),
                "redacted_word_count": sum(
                    1 for word in display_words if has_redaction(word)
                ),
                "wildcard_token_count": sum(
                    1 for span in token_spans if span.is_wildcard
                ),
                "words": [
                    {
                        "word_index": index,
                        "display_text": str(display_word),
                        "alignment_text": normalized_words[index],
                        "wildcard_count": count_wildcards(normalized_words[index]),
                    }
                    for index, display_word in enumerate(display_words)
                    if has_redaction(display_word)
                ],
            },
            "token_timing_evidence": {
                "token_count": len(token_spans),
                "evidence_frame_count": int(evidence_frame_count),
                "path_frame_count": int(path_frame_count),
                "path_to_evidence_ratio": float(path_to_evidence_ratio),
                "posterior_relative_floor": TOKEN_POSTERIOR_RELATIVE_FLOOR,
                "token_to_blank_odds_floor": TOKEN_TO_BLANK_ODDS_FLOOR,
            },
            "timeline": {
                "analysis_start_abs": float(timeline.analysis_start_abs),
                "analysis_end_abs": float(timeline.analysis_end_abs),
                "search_start_frame": int(search_start_frame),
                "search_end_frame": int(search_end_frame),
                "search_start_abs": float(alignment_origin_abs),
                "search_end_abs": float(timeline.frame_to_abs(search_end_frame)),
                "inputs_to_logits_ratio": int(inputs_to_logits_ratio),
            },
            "pronunciation": {
                "mode": str(getattr(pronunciation_normalizer, "mode", "")),
                "engine_version": str(
                    getattr(pronunciation_normalizer, "engine_version", "")
                ),
                "converted_word_count": sum(
                    word.strategy != "literal_cyrillic"
                    for word in pronunciation_words
                ),
                "words": [
                    {
                        "word_index": index,
                        "display_text": word.display_text,
                        "alignment_text": normalized_words[index],
                        "strategy": word.strategy,
                        "ipa": word.ipa,
                    }
                    for index, word in enumerate(pronunciation_words)
                    if word.strategy != "literal_cyrillic"
                ],
            },
            "source_separation": dict(separation.diagnostics),
            "words": [
                {
                    "word_index": index,
                    "t_start": float(word.t_start),
                    "t_end": float(word.t_end),
                    "confidence": float(word.confidence),
                }
                for index, word in enumerate(aligned_words)
            ],
        },
        backend={
            "type": "local_ctc_viterbi",
            "algorithm_version": ALIGNMENT_ALGORITHM_VERSION,
            "model_revision": str(model_revision),
            "audio_preprocessor": "demucs",
            "separator_model": str(vocal_separator.model_name),
            "separator_revision": str(vocal_separator.model_revision),
            "separator_package_version": str(vocal_separator.package_version),
            "sample_rate": SAMPLE_RATE,
            "analysis_start_abs": float(analysis_start),
            "requested_analysis_end_abs": float(requested_analysis_end),
            "actual_analysis_end_abs": float(timeline.analysis_end_abs),
            "seconds_per_emission_frame": float(seconds_per_frame),
            "emission_frames": int(log_probs.shape[0]),
            "search_start_frame": int(search_start_frame),
            "search_end_frame": int(search_end_frame),
            "search_start_abs": float(selected_candidate.search_start_abs),
            "search_end_abs": float(selected_candidate.search_end_abs),
            "target_tokens": len(target_ids),
            "path_log_score": float(path_score),
            "pronunciation_mode": str(
                getattr(pronunciation_normalizer, "mode", "")
            ),
            "pronunciation_engine_version": str(
                getattr(pronunciation_normalizer, "engine_version", "")
            ),
        },
    )
