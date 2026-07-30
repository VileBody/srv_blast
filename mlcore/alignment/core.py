from __future__ import annotations

import json
import math
import re
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
    ERROR_INTERNAL,
    ERROR_MODEL_UNAVAILABLE,
    ERROR_TIMEOUT,
    ERROR_UNSUPPORTED_TEXT,
    ERROR_WINDOW_MISMATCH,
)

SAMPLE_RATE = 16_000
TOKEN_POSTERIOR_RELATIVE_FLOOR = 0.2
TOKEN_TO_BLANK_ODDS_FLOOR = 0.1
_EDGE_PUNCTUATION_RE = re.compile(r"^[^\w]+|[^\w]+$", flags=re.UNICODE)


class AlignmentFailure(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.message = str(message)
        super().__init__(f"{self.code}: {self.message}")


@dataclass(frozen=True)
class TokenSpan:
    target_index: int
    token_id: int
    start_frame: int
    end_frame: int
    score: float
    path_start_frame: int
    path_end_frame: int


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
        cleaned = _EDGE_PUNCTUATION_RE.sub("", raw).strip()
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
    if any(token_id < 0 or token_id >= vocab_size for token_id in targets):
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
        # Viterbi can hold a token state through unrelated frames merely to
        # preserve a path. Keep the connected posterior-supported region around
        # the strongest token-vs-blank peak as the acoustic timing evidence.
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
    for candidate in candidates:
        if all(character in vocab for character in set("".join(candidate))):
            return candidate

    best_candidate = min(
        candidates,
        key=lambda candidate: len(
            {
                character
                for character in "".join(candidate)
                if character not in vocab
            }
        ),
    )
    missing = sorted(
        character
        for character in set("".join(best_candidate))
        if character not in vocab
    )
    error_display_words = display_words or pronunciation_words
    unsupported_words = [
        str(display_word)
        for display_word, pronunciation_word in zip(
            error_display_words,
            best_candidate,
        )
        if any(character not in vocab for character in pronunciation_word)
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
        encoded = [int(item) for item in tokenizer(word, add_special_tokens=False).input_ids]
        if not encoded or (unk_id is not None and int(unk_id) in encoded):
            raise AlignmentFailure(
                ERROR_UNSUPPORTED_TEXT,
                f"word contains unsupported model tokens index={word_index}",
            )
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
                confidence=float(np.mean([span.score for span in word_spans])),
            )
        )
    return output



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
) -> AlignmentResult:
    display_words = reference_words(target_fragment)
    with tempfile.TemporaryDirectory(prefix="blast_alignment_") as temp_dir_raw:
        crop_path = Path(temp_dir_raw) / "analysis_crop.wav"
        analysis_start, requested_analysis_end = extract_analysis_crop(
            ffmpeg_bin=ffmpeg_bin,
            audio_path=Path(audio_path),
            output_path=crop_path,
            clip_start_abs=float(clip_start_abs),
            clip_end_abs=float(clip_end_abs),
            padding_left_sec=float(padding_left_sec),
            padding_right_sec=float(padding_right_sec),
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
    token_spans, path_score, search_start_frame, search_end_frame = (
        align_targets_in_window(
            log_probs,
            target_ids,
            blank_id=blank_id,
            timeline=timeline,
            clip_start_abs=float(clip_start_abs),
            clip_end_abs=float(clip_end_abs),
        )
    )
    seconds_per_frame = timeline.seconds_per_frame
    alignment_origin_abs = timeline.frame_to_abs(search_start_frame)
    aligned_words = aggregate_words(
        display_words=display_words,
        normalized_words=normalized_words,
        spans=token_spans,
        token_word_indexes=token_word_indexes,
        seconds_per_frame=seconds_per_frame,
        analysis_start_abs=alignment_origin_abs,
    )
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
