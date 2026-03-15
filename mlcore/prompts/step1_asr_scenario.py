from __future__ import annotations

from core.clip_window import CLIP_WINDOW_RANGE_S_LABEL


SYSTEM_PART = f"""
========================
STAGE 1 — ASR + SCENARIO DRAFT
========================
You receive ONE audio track.

Return JSON for Stage1PlanPayload:
1) audio window on full-track timeline (absolute seconds):
   - audio.clip_start_abs
   - audio.clip_end_abs
   - audio.moment_of_interest_sec (optional)
2) transcript_words: word-level ASR list for the full-track timeline:
   - each item: {text, t_start, t_end}
3) draft_blocks: rough editorial scenario over 7 blocks:
   - phrases list per block part
   - block_5 must be split to slowly_in / fast_reveal / glitch_peak / mine

Hard constraints:
- audio window duration must be {CLIP_WINDOW_RANGE_S_LABEL}.
- clip_end_abs > clip_start_abs.
- transcript word timings must be monotonic and valid (t_end > t_start).
- Return valid JSON only, no markdown/comments.
"""
