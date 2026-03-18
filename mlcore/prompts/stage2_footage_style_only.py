from __future__ import annotations

import re
from pathlib import Path


def _extract_reference_prompt_body() -> str:
    src = Path(__file__).resolve().parents[2] / "3rd_footage_selection_prompt" / "prompt.md"
    if not src.exists():
        raise RuntimeError(f"Stage2B reference prompt source missing: {src}")
    raw = src.read_text(encoding="utf-8")

    match = re.search(r'SYSTEM_PART\s*=\s*r?"""(.*?)"""', raw, flags=re.S)
    if not match:
        raise RuntimeError(f"Failed to extract SYSTEM_PART from {src}")

    body = str(match.group(1) or "").strip()
    if not body:
        raise RuntimeError(f"Empty SYSTEM_PART body in {src}")

    # Canonical field name for stage2 style contract.
    body = body.replace("exclude_people", "exclude")
    return body


_REFERENCE_BODY = _extract_reference_prompt_body()

SYSTEM_PART = (
    r"""
===============================
STAGE 2B — FOOTAGE STYLE PICK
===============================
Return ONLY raw JSON matching Stage2FootageStyleRawPayload.
No markdown. No comments. No extra keys.

Raw schema:
{
  "theme": "<string>",
  "mood": "major|minor",
  "filters": {
    "color_priority": ["dark|light|warm|cold|neutral", "..."],
    "exclude": ["none|girls|guys|couple|crowd|driver", "..."],
    "priority_theme_tags": ["...", "..."]
  }
}

Hard constraints:
- Use `filters.exclude` as canonical field name.
- Output values must be lowercase and deduplicated.
- Keep output as strict JSON object only.

""" + _REFERENCE_BODY + r"""

Contract reminder:
- Output is ONLY Stage2FootageStyleRawPayload JSON.
- Do not output resolved genre/tag here.
"""
)
