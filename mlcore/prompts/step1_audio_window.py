# mlcore/prompts/step1_audio_window.py
from __future__ import annotations

SYSTEM_PART = r"""
========================
STEP 1 — AUDIO WINDOW
========================
You will receive ONE audio track.

Goal:
Select a SINGLE continuous clip window on the FULL TRACK timeline (absolute seconds).
Return ONLY the window. Do NOT output any After Effects layer timing.
AE audio layer timing is derived deterministically in postprocess.

Definitions:
- FULL TRACK timeline: absolute seconds in the original audio file.
- COMP timeline: will be derived as 0..(clip_end_abs-clip_start_abs).

You must output an object "audio" with fields:
- clip_start_abs: number (>=0), absolute seconds on FULL TRACK
- clip_end_abs: number (> clip_start_abs), absolute seconds on FULL TRACK
- moment_of_interest_sec: number|null (optional marker on FULL TRACK)

Hard constraints:
- Duration MUST be 15..25 seconds (prefer 18..20 if uncertain).
- clip_end_abs > clip_start_abs.
"""
