from __future__ import annotations

PROMPT_VERSION = "v1"

from pathlib import Path


_REF_PROMPT_PATH = Path(__file__).resolve().parents[2] / "3rd_template" / "prompt_jakson.md"
SCENES_3RD_SINGLE_STEP_PROMPT_BODY = _REF_PROMPT_PATH.read_text(encoding="utf-8")

_TECH_APPENDIX = r"""
---
TECHNICAL PIPELINE CONTRACT (mandatory):
- Ignore any requirement about markdown code blocks from the reference text.
- Return ONLY raw JSON matching Scenes3rdPayload:
  {
    "clip": {"start": <float>, "end": <float>},
    "scenes": [...]
  }
- Use attached audio as timing source and REFERENCE_TEXT from user prompt as lexical source.
- clip.start MUST equal stage1.audio.clip_start_abs EXACTLY.
- clip.end MUST equal stage1.audio.clip_end_abs EXACTLY.
- All scene/start/end/word_timings values are ABSOLUTE full-track seconds.
- No markdown, no comments, no extra keys.
- TYPE_4 contract:
  - reason is REQUIRED and non-empty.
  - target duration >= 3.0s; if shorter, reason must explain why it cannot be safely extended.
  - absolute minimum duration >= 0.44s.
"""

SYSTEM_PART = SCENES_3RD_SINGLE_STEP_PROMPT_BODY.rstrip() + "\n\n" + _TECH_APPENDIX.strip() + "\n"

