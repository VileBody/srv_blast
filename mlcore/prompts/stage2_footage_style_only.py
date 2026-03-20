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
Return ONLY raw JSON matching Stage2FootageStyleRotation.
No markdown. No comments. No extra keys.

Schema:
{
  "subgroups": [
    {
      "theme": "<string>",
      "mood": "major|minor",
      "tags_group": "<string>",
      "filters": {
        "color_priority": ["dark|light|warm|cold|neutral", "..."],
        "exclude": ["none|girls|guys|couple|crowd|driver", "..."],
        "exclude_tags": ["...", "..."],
        "require_people": "girls|guys|couple|crowd|driver|none",
        "priority_theme_tags": ["...", "..."]
      }
    }
  ]
}

Hard constraints:
- `subgroups` must contain 2–3 entries (or 1 if the theme only has one meaningful subgroup).
- All subgroups must share the SAME `theme` and `mood`.
- Each subgroup must target a DIFFERENT `tags_group` from THEMES LOGIC (no duplicates).
- Subgroup order defines rotation order: first block uses subgroups[0], second uses subgroups[1], etc.
- Use `filters.exclude` as canonical field name for people exclusion.
- `filters.exclude_tags` must contain ALL _exclude_tags from the chosen group (empty list [] if none).
- `tags_group` must be the exact name of the chosen subgroup from THEMES LOGIC.
- `require_people` must be included ONLY if the chosen group has "_people" field; omit otherwise.
- Output values must be lowercase and deduplicated.
- Keep output as strict JSON object only.

""" + _REFERENCE_BODY + r"""

Contract reminder:
- Output is ONLY Stage2FootageStyleRotation JSON (object with "subgroups" array).
- Do not output resolved genre/tag here.
"""
)
