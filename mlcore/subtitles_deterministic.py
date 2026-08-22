from __future__ import annotations

"""Deterministic, no-LLM materialization of every Stage-2 subtitle mode.

The module deliberately uses only Stage-1 word timings and draft block sizes.
There are no language/model/network fallbacks: malformed or insufficient input
fails with an operator-facing error.
"""

from dataclasses import dataclass
import logging
import re
from statistics import mean, median
from typing import Iterable, Sequence

from core.subtitles_mode import (
    SUBTITLES_MODE_IMPULSE_2ND,
    SUBTITLES_MODE_LEGACY_BLOCKS,
    SUBTITLES_MODE_SCENES_3RD,
    SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
    SUBTITLES_MODE_TEMPLATE_4TH,
    normalize_subtitles_mode,
)
from mlcore.models.stage1_plan import Stage1PlanPayload
from mlcore.models.subtitles_flow import (
    Impulse2ndRawPayload,
    Scenes3rdPayload,
    Scenes3rdSingleStepPayload,
    SubtitleFlowPlan,
    Template4Payload,
)
from mlcore.models.subtitles_tokens import BlocksTokensPayload
from mlcore.subtitles_flow.planner import SubtitlesPlannerFactory


_EDGE_PUNCTUATION = " \t\r\n.,!?;:…\"“”«»„—–/()[]{}"
_BRACKETED_RE = re.compile(r"^[\[(].*[\])]$")
_NO_SPEECH = {"no_speech", "nospeech", "silence", "noaudio"}
_STOPWORDS = {
    # Only function words are excluded from duration-based emphasis. The list is
    # intentionally small; unknown languages remain fully timing-driven.
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "i", "in",
    "is", "it", "of", "on", "or", "the", "to", "we", "with", "you",
    "а", "без", "бы", "в", "во", "да", "для", "до", "и", "из", "или",
    "к", "как", "на", "не", "но", "о", "от", "по", "с", "со", "у", "я",
}


@dataclass(frozen=True)
class _Word:
    text: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _canonical(text: str) -> str:
    return str(text or "").strip(_EDGE_PUNCTUATION).casefold()


def _clean_word(text: str, *, lowercase: bool = False) -> str:
    raw = str(text or "").strip()
    if not raw or _BRACKETED_RE.fullmatch(raw):
        return ""
    value = raw.strip(_EDGE_PUNCTUATION)
    return value.casefold() if lowercase else value


def _words_in_clip(stage1: Stage1PlanPayload, *, clean: bool, lowercase: bool = False) -> list[_Word]:
    clip_start = float(stage1.audio.clip_start_abs)
    clip_end = float(stage1.audio.clip_end_abs)
    words: list[_Word] = []
    for item in sorted(stage1.transcript_words, key=lambda w: (float(w.t_start), float(w.t_end))):
        start = max(clip_start, float(item.t_start))
        end = min(clip_end, float(item.t_end))
        if end <= start:
            continue
        text = _clean_word(item.text, lowercase=lowercase) if clean else str(item.text).strip()
        if text:
            words.append(_Word(text=text, start=start, end=end))
    if not words:
        raise ValueError(
            "stage2 subtitles deterministic: stage1 has no usable transcript_words inside "
            f"clip {clip_start:.3f}..{clip_end:.3f}"
        )
    return words


def _is_no_speech(words: Sequence[_Word]) -> bool:
    return len(words) == 1 and _canonical(words[0].text).replace(" ", "") in _NO_SPEECH


def _phrase_words(value: Iterable[str]) -> int:
    return max(1, sum(len(str(part).replace("\r", " ").split()) for part in value))


def _legacy_specs(stage1: Stage1PlanPayload) -> list[tuple[str, list[str]]]:
    d = stage1.draft_blocks
    return [
        ("block_1", d.block_1.phrases),
        ("block_2.p1", d.block_2.p1.phrases),
        ("block_2.p2", d.block_2.p2.phrases),
        ("block_3", d.block_3.phrases),
        ("block_4.p1", d.block_4.p1.phrases),
        ("block_4.p2", d.block_4.p2.phrases),
        ("block_5.slowly_in", d.block_5.slowly_in.phrases),
        ("block_5.fast_reveal", d.block_5.fast_reveal.phrases),
        ("block_5.glitch_peak", d.block_5.glitch_peak.phrases),
        ("block_5.mine", d.block_5.mine.phrases),
        ("block_6", d.block_6.phrases),
        ("block_7.part1", d.block_7.part1.phrases),
        ("block_7.part2", d.block_7.part2.phrases),
    ]


def _legacy_counts(total: int, desired: Sequence[int]) -> list[int]:
    mine_idx = 9
    max_counts = [1 if i == mine_idx else 8 for i in range(len(desired))]
    min_total = len(desired)
    max_total = sum(max_counts)
    if not min_total <= total <= max_total:
        raise ValueError(
            "stage2 subtitles deterministic legacy_blocks requires "
            f"{min_total}..{max_total} timed words; got {total}"
        )
    counts = [1] * len(desired)
    for _ in range(total - min_total):
        candidates = [i for i, count in enumerate(counts) if count < max_counts[i]]
        if not candidates:
            raise AssertionError("legacy count allocation exhausted capacity")
        idx = max(
            candidates,
            key=lambda i: (desired[i] - counts[i], desired[i], -counts[i], -i),
        )
        counts[idx] += 1
    return counts


def _token_rows(words: Sequence[_Word]) -> list[dict]:
    return [
        {
            "text": word.text,
            "t_start": word.start,
            "t_end": word.end,
            "trailing": "" if i == len(words) - 1 else " ",
        }
        for i, word in enumerate(words)
    ]


def _build_legacy(stage1: Stage1PlanPayload) -> BlocksTokensPayload:
    words = _words_in_clip(stage1, clean=False)
    specs = _legacy_specs(stage1)
    if _is_no_speech(words):
        chunks = [[words[0]] for _ in specs]
    else:
        desired = [_phrase_words(phrases) for _, phrases in specs]
        counts = _legacy_counts(len(words), desired)
        chunks: list[list[_Word]] = []
        cursor = 0
        for count in counts:
            chunks.append(words[cursor : cursor + count])
            cursor += count

    rows: dict[str, dict] = {}
    for (path, draft_phrases), chunk in zip(specs, chunks, strict=True):
        phrase = " ".join(str(x).strip() for x in draft_phrases if str(x).strip())
        if path == "block_5.mine":
            phrase = chunk[0].text
        if not phrase:
            phrase = " ".join(word.text for word in chunk)
        rows[path] = {"phrase": phrase, "tokens": _token_rows(chunk)}

    return BlocksTokensPayload.model_validate(
        {
            "clip": {
                "start": float(stage1.audio.clip_start_abs),
                "end": float(stage1.audio.clip_end_abs),
            },
            "block_1": rows["block_1"],
            "block_2": {"p1": rows["block_2.p1"], "p2": rows["block_2.p2"]},
            "block_3": rows["block_3"],
            "block_4": {"p1": rows["block_4.p1"], "p2": rows["block_4.p2"]},
            "block_5": {
                "slowly_in": rows["block_5.slowly_in"],
                "fast_reveal": rows["block_5.fast_reveal"],
                "glitch_peak": rows["block_5.glitch_peak"],
                "mine": rows["block_5.mine"],
            },
            "block_6": rows["block_6"],
            "block_7": {"part1": rows["block_7.part1"], "part2": rows["block_7.part2"]},
        }
    )


def _split_words(
    words: Sequence[_Word],
    *,
    max_words: int,
    max_chars: int,
    hard_gap: float = 0.4,
) -> list[list[_Word]]:
    groups: list[list[_Word]] = []
    current: list[_Word] = []
    for word in words:
        projected = len(" ".join([*(w.text for w in current), word.text]))
        gap = word.start - current[-1].end if current else 0.0
        should_split = bool(current) and (
            gap >= hard_gap or len(current) >= max_words or projected > max_chars
        )
        if should_split:
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


def _is_content_word(word: _Word) -> bool:
    return _canonical(word.text) not in _STOPWORDS and len(_canonical(word.text)) >= 2


def _emphasis_threshold(words: Sequence[_Word]) -> float:
    durations = [word.duration for word in words]
    return max(0.4, mean(durations), median(durations) * 1.25)


def _consecutive_repeat_indexes(words: Sequence[_Word]) -> set[int]:
    repeated: set[int] = set()
    start = 0
    while start < len(words):
        key = _canonical(words[start].text)
        end = start + 1
        while end < len(words) and _canonical(words[end].text) == key:
            end += 1
        if key and end - start >= 3:
            repeated.update(range(start, end))
        start = end
    return repeated


def _build_impulse_raw(stage1: Stage1PlanPayload) -> Impulse2ndRawPayload:
    words = _words_in_clip(stage1, clean=True, lowercase=True)
    anchor = words[0].start
    repeated_indexes = _consecutive_repeat_indexes(words)
    repeated_ids = {id(words[i]) for i in repeated_indexes}
    groups = _split_words(words, max_words=4, max_chars=18)

    expanded: list[list[_Word]] = []
    for group in groups:
        run: list[_Word] = []
        for word in group:
            if id(word) in repeated_ids:
                if run:
                    expanded.append(run)
                    run = []
                expanded.append([word])
            else:
                run.append(word)
        if run:
            expanded.append(run)

    threshold = _emphasis_threshold(words)
    peeled: list[list[_Word]] = []
    for group in expanded:
        last = group[-1]
        prefix_duration = last.start - group[0].start
        if (
            len(group) >= 2
            and prefix_duration >= 0.6
            and last.duration >= threshold
            and _is_content_word(last)
        ):
            peeled.extend([group[:-1], [last]])
        else:
            peeled.append(group)

    segments: list[dict] = []
    previous_short = False
    for i, group in enumerate(peeled):
        first, last = group[0], group[-1]
        is_repeat = len(group) == 1 and id(first) in repeated_ids
        next_start = peeled[i + 1][0].start if i + 1 < len(peeled) else None
        hold = (next_start - first.start) if next_start is not None else (last.end + 0.5 - first.start)
        candidate = (
            len(group) <= 2
            and first.duration >= threshold
            and hold >= 0.4
            and _is_content_word(first)
        )
        is_short = is_repeat or (candidate and not previous_short)
        kind = "short" if is_short else "long"
        reason = "refrain" if is_repeat else ("timing_emphasis" if is_short else "timing_or_quota")
        out_abs = next_start if next_start is not None else last.end + 0.5
        segments.append(
            {
                "text": " ".join(word.text for word in group),
                "in": first.start - anchor,
                "out": out_abs - anchor,
                "type": kind,
                "reason": reason,
                "word_timings": [
                    {"word": word.text, "start": word.start - anchor, "end": word.end - anchor}
                    for word in group
                ],
            }
        )
        previous_short = is_short and not is_repeat

    return Impulse2ndRawPayload.model_validate(
        {
            "anchor_in_abs": anchor,
            "word_timings": [
                {"word": word.text, "start": word.start - anchor, "end": word.end - anchor}
                for word in words
            ],
            "segments": segments,
        }
    )


def _best_line_split(words: Sequence[_Word], *, max_line_chars: int) -> list[list[str]]:
    texts = [word.text for word in words]
    if len(texts) <= 1:
        return [texts]
    best: tuple[tuple[int, int, int], int] | None = None
    for idx in range(1, len(texts)):
        left = len(" ".join(texts[:idx]))
        right = len(" ".join(texts[idx:]))
        score = (max(0, left - max_line_chars) + max(0, right - max_line_chars), abs(left - right), idx)
        if best is None or score < best[0]:
            best = (score, idx)
    assert best is not None
    idx = best[1]
    return [texts[:idx], texts[idx:]]


def _hook_group_keys(groups: Sequence[Sequence[_Word]]) -> set[tuple[str, ...]]:
    counts: dict[tuple[str, ...], int] = {}
    for group in groups:
        if len(group) <= 2:
            key = tuple(_canonical(word.text) for word in group)
            counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if key and count >= 3}


def _build_scenes_raw(stage1: Stage1PlanPayload, *, single_step: bool):
    words = _words_in_clip(stage1, clean=True)
    groups = _split_words(words, max_words=5, max_chars=27)
    hook_keys = _hook_group_keys(groups)
    threshold = _emphasis_threshold(words)
    scenes: list[dict] = []
    type4_non_hook_count = 0

    for i, group in enumerate(groups, start=1):
        texts = [word.text for word in group]
        duration = group[-1].end - group[0].start
        gaps = [group[j].start - group[j - 1].end for j in range(1, len(group))]
        max_gap = max(gaps, default=0.0)
        last_gap = gaps[-1] if gaps else 0.0
        even = all(gap < 0.2 for gap in gaps)
        hook = tuple(_canonical(word.text) for word in group) in hook_keys
        peak_idx = max(range(len(group)), key=lambda j: (group[j].duration, len(group[j].text), -j))
        peak = group[peak_idx]
        standout = peak.duration >= threshold and _is_content_word(peak)

        scene_type = "TYPE_1"
        focus_word = None
        focus_style = None
        reason = "default_timing"
        lines = _best_line_split(group, max_line_chars=13)

        if hook and len(group) <= 2:
            scene_type = "TYPE_4"
            focus_word = " ".join(texts)
            focus_style = "red"
            reason = "repeating_hook_3plus"
            lines = [texts]
        elif len(group) <= 2 and duration >= 0.44 and type4_non_hook_count == 0:
            scene_type = "TYPE_4"
            focus_word = " ".join(texts)
            focus_style = "red"
            reason = "isolated_timed_phrase"
            lines = [texts]
            type4_non_hook_count += 1
        elif len(group) in {3, 4} and last_gap >= 0.25 and 3 <= len(texts[-1]) <= 8 and len(" ".join(texts)) <= 27:
            scene_type = "TYPE_3"
            focus_word = texts[-1]
            reason = "measured_last_gap"
            lines = [texts]
        elif len(group) in {4, 5} and duration > 3.0 and even:
            scene_type = "TYPE_5"
            reason = "long_even_flow"
        elif len(group) in {4, 5} and 1.5 <= duration <= 3.5 and standout:
            scene_type = "TYPE_2"
            focus_word = peak.text
            focus_style = "italic"
            reason = "duration_outlier_focus"
            split = peak_idx + 1
            if 0 < split < len(group):
                lines = [texts[:split], texts[split:]]
        elif len(group) in {3, 4, 5} and 1.5 <= duration <= 4.0 and max_gap < 0.25 and len(lines) == 2:
            scene_type = "TYPE_6"
            reason = "even_two_line_groups"

        if len(scenes) >= 3 and all(scene["type"] == scene_type for scene in scenes[-3:]):
            scene_type = "TYPE_1"
            focus_word = None
            focus_style = None
            reason = "variety_cap_reset"

        scenes.append(
            {
                "id": i,
                "type": scene_type,
                "words": texts,
                "start": group[0].start,
                "end": group[-1].end,
                "lines": lines,
                "focus_word": focus_word,
                "focus_style": focus_style,
                "reason": reason,
                "word_timings": [
                    {"word": word.text, "start": word.start, "end": word.end} for word in group
                ],
            }
        )

    model = Scenes3rdSingleStepPayload if single_step else Scenes3rdPayload
    return model.model_validate(
        {
            "clip": {
                "start": float(stage1.audio.clip_start_abs),
                "end": float(stage1.audio.clip_end_abs),
            },
            "scenes": scenes,
        }
    )


def _build_template4_raw(stage1: Stage1PlanPayload) -> Template4Payload:
    words = _words_in_clip(stage1, clean=True)
    groups = _split_words(words, max_words=4, max_chars=25)
    threshold = mean(word.duration for word in words)
    focus_ids = {
        id(word) for word in words if word.duration > threshold and _is_content_word(word)
    }
    # The visual contract requires at least one focus word in each pair of
    # subtitles. Select the longest timed content word when the strict
    # above-average rule produced none for that pair.
    for start in range(0, len(groups), 2):
        pair = groups[start : start + 2]
        if any(id(word) in focus_ids for group in pair for word in group):
            continue
        candidates = [word for group in pair for word in group if _is_content_word(word)]
        if not candidates:
            candidates = [word for group in pair for word in group]
        selected = max(candidates, key=lambda word: (word.duration, len(word.text), -word.start))
        focus_ids.add(id(selected))

    return Template4Payload.model_validate(
        {
            "word_timings": [
                {
                    "word": word.text,
                    "start": word.start,
                    "end": word.end,
                    "focus": id(word) in focus_ids,
                }
                for word in words
            ],
            "subtitles": [
                {
                    "text": " ".join(word.text for word in group).upper(),
                    "in": group[0].start,
                    "out": group[-1].end,
                }
                for group in groups
            ],
        }
    )


def build_subtitles_deterministic(
    *,
    stage1: Stage1PlanPayload,
    subtitles_mode: str,
    logger: logging.Logger,
) -> BlocksTokensPayload | SubtitleFlowPlan:
    """Build and validate the Stage-2 subtitle payload without any LLM call."""

    mode = normalize_subtitles_mode(subtitles_mode)
    if mode == SUBTITLES_MODE_LEGACY_BLOCKS:
        result: BlocksTokensPayload | SubtitleFlowPlan = _build_legacy(stage1)
    elif mode == SUBTITLES_MODE_IMPULSE_2ND:
        planner = SubtitlesPlannerFactory.create(mode)
        result = planner.normalize_payload(payload=_build_impulse_raw(stage1), stage1=stage1, logger=logger)
    elif mode in {SUBTITLES_MODE_SCENES_3RD, SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP}:
        planner = SubtitlesPlannerFactory.create(mode)
        result = planner.normalize_payload(
            payload=_build_scenes_raw(
                stage1,
                single_step=mode == SUBTITLES_MODE_SCENES_3RD_SINGLE_STEP,
            ),
            stage1=stage1,
            logger=logger,
        )
    elif mode == SUBTITLES_MODE_TEMPLATE_4TH:
        planner = SubtitlesPlannerFactory.create(mode)
        result = planner.normalize_payload(payload=_build_template4_raw(stage1), stage1=stage1, logger=logger)
    else:
        raise RuntimeError(
            "stage2 subtitles deterministic called for a mode without this stage: "
            f"{mode!r}"
        )

    segment_count = 13 if isinstance(result, BlocksTokensPayload) else len(result.segments)
    logger.info(
        "stage2_subtitles_deterministic mode=%s segments=%d words=%d (no LLM)",
        mode,
        segment_count,
        len(stage1.transcript_words),
    )
    return result
