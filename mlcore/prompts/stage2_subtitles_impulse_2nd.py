from __future__ import annotations

PROMPT_VERSION = "v1"

from pathlib import Path


_REF_PROMPT_PATH = Path(__file__).resolve().parents[2] / "2nd_template" / "impulse_prompt.md"
IMPULSE_PROMPT_BODY = _REF_PROMPT_PATH.read_text(encoding="utf-8")

_TECH_APPENDIX = r"""
---
TECHNICAL PIPELINE CONTRACT (mandatory):
- Return a single JSON object matching Impulse2ndRawPayload:
  {
    "anchor_in_abs": <float>,
    "word_timings": [{"word": "...", "start": <float>, "end": <float>}, ...],
    "segments": [{"text": "...", "in": <float>, "out": <float>, "type": "long|short", "reason": "...", "word_timings": [...]}, ...]
  }
- Include "reason" for every segment.
  Recommended reason tags:
  - For short: "emphasis_word", "refrain", "imperative"
  - For long: "descriptive_phrase", "timing_constraint", "line_integrity", "quota_limit"
- anchor_in_abs is ABSOLUTE full-track seconds for the first subtitle-layer in-point before normalization.
- Every start/end/in/out in word_timings and segments MUST be normalized:
  normalized_time = absolute_time - anchor_in_abs
- Keep segment order on timeline and enforce out > in for every segment.
- Keep per-segment and top-level word_timings timeline-consistent with the same anchor.
- No markdown, no comments, no extra keys.
"""

SYSTEM_PART = IMPULSE_PROMPT_BODY.rstrip() + "\n\n" + _TECH_APPENDIX.strip() + "\n"
