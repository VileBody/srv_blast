from __future__ import annotations

from pathlib import Path


_V2_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "footage_v2.py"


def _load_v2_body() -> str:
    """Load SYSTEM_PART from footage_v2.py at module level."""
    if not _V2_PROMPT_PATH.exists():
        raise RuntimeError(f"Stage2B v2 prompt source missing: {_V2_PROMPT_PATH}")
    raw = _V2_PROMPT_PATH.read_text(encoding="utf-8")
    # Extract the content between triple quotes after SYSTEM_PART =
    import re
    match = re.search(r'SYSTEM_PART\s*=\s*r?"""(.*?)"""', raw, flags=re.S)
    if match:
        body = str(match.group(1) or "").strip()
        if body:
            return body
    # Fallback: use entire file content if it doesn't follow SYSTEM_PART pattern
    raise RuntimeError(f"Failed to extract SYSTEM_PART from {_V2_PROMPT_PATH}")


_V2_BODY = _load_v2_body()


SYSTEM_PART_V2 = (
    r"""
===============================
STAGE 2B — FOOTAGE STYLE PICK (v2 — Artist-Constrained)
===============================
Return ONLY raw JSON matching Stage2FootageStyleRotation.
No markdown. No comments. No extra keys.

Schema:
{
  "subgroups": [
    {
      "artist_id": "<string>",
      "theme": "<string>",
      "mood": "major|minor",
      "tags_group": "<string>",
      "filters": {
        "color_priority": ["dark|light|warm|cold|neutral", "..."],
        "exclude_people": ["none|girls|guys|couple|crowd|driver", "..."],
        "exclude_tags": ["...", "..."],
        "priority_theme_tags": ["...", "..."]
      }
    }
  ]
}

Hard constraints:
- `subgroups` must contain 1–3 entries.
- All subgroups must share the SAME `artist_id` and `mood`.
- Subgroups may come from different themes of the chosen artist profile.
- Each subgroup must target a DIFFERENT (`theme`, `tags_group`) pair from THEMES LOGIC.
- Subgroup order defines strict priority order for picker:
  - first subgroup = highest priority,
  - when its suitable unseen clips are exhausted, picker moves to next subgroup.
- `filters.exclude_people` is the canonical field name for people exclusion.
- `filters.exclude_tags` must contain ALL _exclude_tags from the chosen group (empty list [] if none).
- `tags_group` must be the exact name of the chosen subgroup from THEMES LOGIC.
- Output values must be lowercase and deduplicated.
- Keep output as strict JSON object only.

""" + _V2_BODY + r"""

Contract reminder:
- Output is ONLY Stage2FootageStyleRotation JSON (object with "subgroups" array).
- Do not output resolved genre/tag here.
"""
)
