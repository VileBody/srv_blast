# mlcore/prompts/step3_footage.py
from __future__ import annotations

SYSTEM_PART = r"""
========================
STEP 3 — FOOTAGE PLAN
========================
You must choose a sequence of footage clips that covers the AUDIO WINDOW on the FULL TRACK timeline:
  [ audio.clip_start_abs .. audio.clip_end_abs ]  (ABSOLUTE seconds)

IMPORTANT:
- Your output clip timings (in_point/out_point/start_time) are ABSOLUTE full-track seconds.
- In postprocess, we will shift them to clip-zero by subtracting audio.clip_start_abs
  to obtain COMP timeline [0 .. duration].

You will be given:
- audio clip window (absolute)
- a list of available footage assets ("assets"), each has:
  { file_name, src_w, src_h, duration_sec }
- ONE attached descriptions bundle file (JSON) with metadata by file_name
  (summary/tags/camera/visuals/objects/composition + duration_sec)

Output requirements:
- Use ONLY file_name values from the provided assets list.
- Each clip must have: out_point > in_point.
- start_time MUST equal in_point exactly (we do not time-remap here).
- Clips must be sorted by in_point.

NO GAPS RULE (IMPORTANT):
- If allow_gaps=false, the coverage MUST be continuous and exact:
    first.in_point MUST equal audio.clip_start_abs
    last.out_point MUST equal audio.clip_end_abs
    for every adjacent pair:
        next.in_point MUST equal prev.out_point (no spaces between clips)
  Use exact decimals (tolerance 1e-6). Do NOT leave uncovered time.

DURATION FEASIBILITY RULE (IMPORTANT):
- You are NOT allowed to pick a clip longer than the source footage file.
  For each clip:
      (out_point - in_point) MUST be <= asset.duration_sec for that file_name.
- If the audio window is long and no single asset can cover a large span,
  you MUST split into more (smaller) clips that fit into available durations.
  Increase the number of clips until all clip durations are feasible.

Soft goals:
- Use descriptions bundle to match mood/meaning.
- Avoid overly busy footage under dense text.
- Keep number of clips reasonable (typically 3–8 for 15–25s), BUT
  if duration constraints force more clips, prefer correctness (no gaps) over fewer clips.
"""
