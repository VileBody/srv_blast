# mlcore/prompts/step1_audio_window.py
from __future__ import annotations

SYSTEM_PART = r"""
========================
STEP 1 — AUDIO WINDOW
========================
You will receive ONE audio track.

Goal:
Select a SINGLE continuous clip window on the FULL TRACK timeline (absolute seconds),
and provide After Effects layer timing parameters so the chosen audio segment plays
exactly in the comp timeline.

Definitions:
- FULL TRACK timeline: absolute seconds in the original audio file.
- COMP timeline: seconds inside After Effects comp (we use 0..duration).

You must output an object "audio" with fields:
- clip_start_abs: number (>=0), absolute seconds on FULL TRACK
- clip_end_abs: number (> clip_start_abs), absolute seconds on FULL TRACK
- layer_start_time: number (AE Layer.startTime)
- layer_in_point: number (>=0), AE Layer.inPoint in COMP seconds
- layer_out_point: number (> layer_in_point), AE Layer.outPoint in COMP seconds
- moment_of_interest_sec: number|null (optional marker on FULL TRACK)

Hard constraints:
- Duration should be 15..25 seconds (prefer 18..20 if uncertain).
- clip_end_abs > clip_start_abs.
- layer_out_point - layer_in_point MUST equal clip_end_abs - clip_start_abs (tolerance 0.10s).
- layer_start_time should be approximately: -clip_start_abs + layer_in_point (tolerance 0.35s).
- If layer_in_point is 0.0, layer_start_time is typically negative.
"""
