from __future__ import annotations

PROMPT_VERSION = "v1"

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
- word_timings MUST contain EVERY word from the transcript. Do NOT omit word_timings.
- FOCUS WORDS ARE CRITICAL — they are colored RED in the final video:
  * You MUST set "focus": true for at least 1 word in every 2 subtitles.
  * Pick emotionally strong, key words. More focus words are better than fewer.
  * For every word_timing with focus=true, that exact word (case-insensitive,
    ignoring punctuation) MUST appear as a space-delimited token in the
    covering subtitle.text.
- Keep subtitle text short enough to fit 2 lines at 900px width, 60px Montserrat-BoldItalic, tracking -25.
  Prefer 2-4 words per subtitle; use 5 only if all words are short.
- No markdown, no comments, no extra keys.
"""

SYSTEM_PART = TEMPLATE_4TH_PROMPT_BODY.rstrip() + "\n\n" + _TECH_APPENDIX.strip() + "\n"

