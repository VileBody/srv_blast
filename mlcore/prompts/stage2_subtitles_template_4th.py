from __future__ import annotations

from pathlib import Path


_REF_PROMPT_PATH = Path(__file__).resolve().parents[2] / "4th_template" / "prompt.md"
TEMPLATE_4TH_PROMPT_BODY = _REF_PROMPT_PATH.read_text(encoding="utf-8")

_TECH_APPENDIX = r"""
---
TECHNICAL PIPELINE CONTRACT (mandatory):
- Return ONLY raw JSON matching Template4Payload:
  {
    "word_timings": [{"word": "...", "start": <float>, "end": <float>, "focus": <bool>}],
    "subtitles": [{"text": "...", "in": <float>, "out": <float>}]
  }
- All start/end/in/out values MUST be ABSOLUTE full-track seconds.
- Keep all subtitles inside stage1 audio clip window.
- subtitle.out must be > subtitle.in for every subtitle.
- For focus words: no more than 2 focused words per subtitle.
- No markdown, no comments, no extra keys.
"""

SYSTEM_PART = TEMPLATE_4TH_PROMPT_BODY.rstrip() + "\n\n" + _TECH_APPENDIX.strip() + "\n"

