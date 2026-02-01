# mlcore/prompts/step3_footage.py
from __future__ import annotations

SYSTEM_PART = r"""
========================
STEP 3 — FOOTAGE PLAN
========================
You must choose a sequence of footage clips that covers the COMP timeline
[ audio.layer_in_point .. audio.layer_out_point ] (usually 0..duration).

You will be given:
- comp_duration_sec (computed from STEP 1 as layer_out_point - layer_in_point)
- a list of available footage assets ("assets"), each has:
  { file_name, src_w, src_h }
- ONE attached descriptions bundle file (JSON) with metadata by file_name
  (summary/tags/camera/visuals/objects/composition)

Output requirements:
- Use ONLY file_name values from the provided assets list.
- Each clip must have: out_point > in_point.
- start_time MUST equal in_point exactly (we do not time-remap here).
- Clips must be sorted by in_point.
- Prefer full coverage without gaps (allow_gaps=false), unless you explicitly set allow_gaps=true.

Soft goals:
- Use descriptions bundle to match mood/meaning.
- Avoid overly busy footage under dense text.
- Keep number of clips reasonable (typically 3–8 for 15–25s).
"""
