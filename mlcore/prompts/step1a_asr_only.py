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

Hard constraints:
- transcript must be in FULL TRACK timeline (absolute seconds from audio start).
- transcript word timings must be monotonic and valid (t_end > t_start).
- Do NOT output scenario blocks or clip window here.
- Return valid JSON only, no markdown/comments.
"""

