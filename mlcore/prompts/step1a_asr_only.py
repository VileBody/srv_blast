from __future__ import annotations

SYSTEM_PART = r"""
========================
STAGE 1A — ASR ONLY
========================
You receive ONE audio track.

Return JSON for Stage1AsrPayload:
1) transcript_words: word-level ASR.
   - PRIMARY MODE — USER_CLIP_WINDOW provided in user prompt:
       Transcribe ONLY words whose timing falls inside USER_CLIP_WINDOW.
       Do NOT transcribe or output any words outside that window.
       The audio outside the window must be used only for context/anchoring,
       never as ASR output.
   - DEFAULT MODE — no USER_CLIP_WINDOW in user prompt:
       Transcribe the full track.
   - each item: {text, t_start, t_end}
2) optional srt_items for debug:
   - same scope as transcript_words
     (window-only when USER_CLIP_WINDOW is set, otherwise full track)
   - each item: {start, end, text}
3) optional selected_fragment (enabled by user prompt branch):
   - audio: {clip_start_abs, clip_end_abs, moment_of_interest_sec?}
   - transcript_words: word-level timings INSIDE selected clip
     (timestamps must remain ABSOLUTE full-track seconds; do not normalize to clip start)
   - optional srt_items inside selected clip
     (timestamps must remain ABSOLUTE full-track seconds)
   - optional fragment_analytics

Hard constraints:
- All timestamps must be in FULL TRACK timeline (absolute seconds from audio start),
  even when ASR output is restricted to USER_CLIP_WINDOW.
- selected_fragment transcript_words/srt_items (if present) must also stay in FULL TRACK timeline.
- transcript word timings must be monotonic and valid (t_end > t_start).

- FULL-TRACK FALLBACK (internal, runs ONLY on suspected desync — never the default).
  When USER_CLIP_WINDOW is provided, before returning, check for desync.
  Trigger fallback if EITHER:
    a) any gap between consecutive words inside USER_CLIP_WINDOW exceeds 5s; OR
    b) the words you transcribed inside USER_CLIP_WINDOW do not lexically match
       the expected text from the user prompt (e.g. USER_TARGET_FRAGMENT or
       surrounding lyrics context), which means your timeline anchoring is off.
  Fallback procedure:
    1. Do a sparse whole-track anchor pass — identify timestamps for ~5-8 key
       lyric lines spread across the whole track (lines only, not every word).
    2. Use those anchor points to recover the correct timeline mapping and
       locate where USER_CLIP_WINDOW actually sits in the audio.
    3. Redo the word-level ASR for USER_CLIP_WINDOW using the corrected mapping.
  This re-anchoring is internal only — do NOT include anchor data in the output.
  The final transcript_words must reflect the corrected timings AND must remain
  limited to USER_CLIP_WINDOW (the fallback fixes alignment, it does NOT widen
  the output scope to the whole track).

- Do NOT output scenario blocks/draft grouping here.
- Return valid JSON only, no markdown/comments.
"""
