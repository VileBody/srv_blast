from __future__ import annotations

PROMPT_VERSION = "v1"

SYSTEM_PART = r"""
========================
STAGE 1A — FORCED WORD ALIGNMENT
========================
You receive ONE audio track and REFERENCE_TEXT.

Return JSON for Stage1ForcedAlignmentPayload:
1) aligned_words: exactly one timed item per reference word:
   - each item: {text, t_start, t_end}
   - t_start/t_end MUST be strings in format mm:ss.mmm (absolute full-track timeline)
2) pause_spans: optional pauses not present in REFERENCE_TEXT:
   - emit item only when silence gap between neighboring words is > 1.0s
   - each item: {text:"[pause]", t_start, t_end}
   - t_start/t_end MUST be strings in format mm:ss.mmm (absolute full-track timeline)
3) optional selected_fragment (enabled by user prompt branch):
   - audio: {clip_start_abs, clip_end_abs, moment_of_interest_sec?}
     where all audio timestamps are strings in format mm:ss.mmm
   - transcript_words: word-level timings INSIDE selected clip
     (t_start/t_end are strings mm:ss.mmm on ABSOLUTE full-track timeline; do not normalize)
   - optional pause_spans inside selected clip
     (t_start/t_end are strings mm:ss.mmm on ABSOLUTE full-track timeline)
   - optional srt_items inside selected clip
     (start/end are strings mm:ss.mmm on ABSOLUTE full-track timeline)
   - optional fragment_analytics

Hard constraints:
- aligned_words length MUST equal REFERENCE_TEXT word count.
- Keep the exact word order from REFERENCE_TEXT.
- Do NOT skip, merge, split, reorder, or invent words.
- Do NOT add backing/ad-lib words that are absent in REFERENCE_TEXT.
- Structural tags such as [pause], [bridge], [hook], [verse] are hints, not words.
- Do NOT output structural tags in aligned_words.
- pause_spans are allowed only for real silences (>1.0s) and MUST stay between neighboring words.
- aligned_words/pause_spans timings must be on FULL TRACK timeline and encoded as mm:ss.mmm strings.
- selected_fragment audio/transcript_words/pause_spans/srt_items (if present) must also use mm:ss.mmm strings on FULL TRACK timeline.
- mm:ss.mmm means EXACTLY 3 digits after dot; do not output extra precision and do not drop digits.
- Do NOT quantize to coarse buckets (.000/.050/.100/.250/etc.) unless acoustically exact.
- Use real measured boundaries from the audio, not synthetic uniform timing grids.
- Every item must satisfy: t_end > t_start.
- Return valid JSON only, no markdown/comments.
"""
