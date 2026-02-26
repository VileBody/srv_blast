from __future__ import annotations

SYSTEM_PART = r"""
===============================
STAGE 2B — FOOTAGE STYLE PICK
===============================
You receive:
- stage1 context (audio clip window + draft blocks)
- style pool groups with aggregate durations:
  { genre, tag, assets_count, total_duration_sec }

Task:
- Produce ONLY FootageStylePickPayload:
  { "genre": "...", "tag": "..." }

Hard constraints:
- Pick exactly one pair (genre, tag) from STYLE_POOL_GROUPS_JSON.
- Do NOT invent genre/tag not present in the pool.
- Prefer a pair whose total_duration_sec can reasonably cover the target window
  (target = stage1.audio.clip_end_abs - stage1.audio.clip_start_abs).
- Do NOT output clip timings or file names here.
"""
