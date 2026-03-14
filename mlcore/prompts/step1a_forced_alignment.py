from __future__ import annotations

SYSTEM_PART = r"""
========================
STAGE 1A — FORCED WORD ALIGNMENT
========================
You receive ONE audio track and REFERENCE_TEXT.

Return JSON for Stage1ForcedAlignmentPayload:
1) aligned_words: exactly one timed item per reference word:
   - each item: {text, t_start, t_end}
2) optional selected_fragment (enabled by user prompt branch):
   - audio: {clip_start_abs, clip_end_abs, moment_of_interest_sec?}
   - transcript_words: word-level timings INSIDE selected clip
   - optional srt_items inside selected clip
   - optional fragment_analytics

Hard constraints:
- aligned_words length MUST equal REFERENCE_TEXT word count.
- Keep the exact word order from REFERENCE_TEXT.
- Do NOT skip, merge, split, reorder, or invent words.
- Do NOT add backing/ad-lib words that are absent in REFERENCE_TEXT.
- Structural tags such as [pause], [bridge], [hook], [verse] are hints, not words.
- Do NOT output structural tags in aligned_words.
- Timings must be on FULL TRACK timeline (absolute seconds from audio start).
- Every item must satisfy: t_end > t_start.
- Return valid JSON only, no markdown/comments.
"""
