from __future__ import annotations

SYSTEM_PART = r"""
========================
STAGE 2B — FOOTAGE ONLY
========================
You receive:
- stage1 audio window (absolute full-track seconds)
- assets allow-list with duration_sec for each file
- optional descriptions bundle metadata

Task:
- Produce ONLY footage payload matching FootageSelectionPayload schema.

Hard constraints:
- Use ONLY provided file_name values.
- Clip timings are ABSOLUTE full-track seconds.
- start_time MUST equal in_point exactly.
- out_point > in_point.
- Coverage must be continuous and exact:
  first.in_point == clip_start_abs
  last.out_point == clip_end_abs
  adjacent clips must be exact seam.
- Clip duration feasibility:
  (out_point - in_point) <= asset.duration_sec for chosen file.
- Keep single-clip spans reasonably short for dynamic editing:
  target each clip <= 4.5 sec.
  If needed, split into more clips to satisfy feasibility and full coverage.
"""
