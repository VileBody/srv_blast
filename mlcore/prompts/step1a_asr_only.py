from __future__ import annotations

SYSTEM_PART = r"""
========================
STAGE 1A — ASR ONLY
========================
You receive ONE audio track.

Return JSON for Stage1AsrPayload:
1) transcript_words: full-track word-level ASR:
   - each item: {text, t_start, t_end}
2) optional srt_items for debug:
   - each item: {start, end, text}
3) optional selected_fragment (enabled by user prompt branch):
   - audio: {clip_start_abs, clip_end_abs, moment_of_interest_sec?}
   - transcript_words: word-level timings INSIDE selected clip
     (timestamps must remain ABSOLUTE full-track seconds; do not normalize to clip start)
   - optional srt_items inside selected clip
     (timestamps must remain ABSOLUTE full-track seconds)
   - optional fragment_analytics

Hard constraints:
- transcript must be in FULL TRACK timeline (absolute seconds from audio start).
- selected_fragment transcript_words/srt_items (if present) must also stay in FULL TRACK timeline.
- transcript word timings must be monotonic and valid (t_end > t_start).
- Before returning, scan all consecutive word pairs in transcript_words.
  If ANY gap between consecutive words exceeds 5s — trigger internal re-anchoring:
    1. Using the full track lyrics and the audio, identify timestamps for ~5-8 key lines
       spread across the whole track (sparse pass — lines only, not every word).
    2. Use those anchor points to determine the correct position of the target fragment
       on the full-track timeline.
    3. Redo the word-level ASR for the fragment using the corrected time window.
  This re-anchoring is internal only — do NOT include anchor data in the output.
  The final transcript_words must reflect the corrected timings.
- Do NOT output scenario blocks/draft grouping here.
- Return valid JSON only, no markdown/comments.
"""
