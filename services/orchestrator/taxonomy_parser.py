"""Parse the footage-selection prompt into a structured taxonomy dict.

The THEMES LOGIC block inside ``3rd_footage_selection_prompt/prompt.md`` is a
Python-compatible dict literal.  We extract it and ``ast.literal_eval`` it into
a dict of ``{theme_name: {color, exclude, tags_groups}}``.

Each *tags_group* value is normalised to an object with at least ``_tags``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Dict, Optional

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "3rd_footage_selection_prompt" / "prompt.md"

_CACHE: Optional[Dict[str, Any]] = None


def _extract_themes_block(text: str) -> str:
    """Return the raw dict literal between 'THEMES LOGIC' and 'STEP 3'.

    The prompt lists themes as ``"theme_name": {...},`` entries (not wrapped
    in an outer ``{...}``).  We locate the first ``"theme":`` line after the
    header, collect everything up to ``STEP 3``, and wrap in braces.
    """
    start = text.find("THEMES LOGIC")
    if start == -1:
        raise ValueError("THEMES LOGIC section not found in prompt")

    end_marker = text.find("STEP 3", start)
    if end_marker == -1:
        end_marker = len(text)

    # Slice from header to STEP 3
    section = text[start:end_marker]

    # Find where themes begin: first line matching `"some_key": {`
    m = re.search(r'^\s*"[^"]+"\s*:\s*\{', section, re.MULTILINE)
    if not m:
        raise ValueError("No theme entries found after THEMES LOGIC header")

    body = section[m.start():]

    body = body.rstrip().rstrip(",").rstrip()

    # Balance braces: wrap body in { } and verify
    candidate = "{" + body + "}"
    opens = candidate.count("{")
    closes = candidate.count("}")
    # If there's one extra close brace, trim the last one from body
    while closes > opens and body.rstrip().endswith("}"):
        body = body.rstrip()
        body = body[:-1].rstrip().rstrip(",").rstrip()
        candidate = "{" + body + "}"
        opens = candidate.count("{")
        closes = candidate.count("}")

    return candidate


def _normalise_group(value: Any) -> Dict[str, Any]:
    """Ensure every tag group is an object with ``_tags``."""
    if isinstance(value, list):
        return {"_tags": value}
    if isinstance(value, dict):
        return value
    return {"_tags": []}


def parse_taxonomy(prompt_path: Path | None = None) -> Dict[str, Any]:
    """Return ``{theme_name: {color, exclude, tags_groups}}``."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    src = prompt_path or _PROMPT_PATH
    raw = src.read_text(encoding="utf-8")

    # The prompt wraps the dict in a raw Python string (SYSTEM_PART = r\"\"\"...\"\"\")
    # Strip the wrapper so we get just the text.
    inner = raw
    m = re.search(r'r"""(.*?)"""', raw, re.DOTALL)
    if m:
        inner = m.group(1)

    block = _extract_themes_block(inner)
    themes: Dict[str, Any] = ast.literal_eval(block)

    # Normalise tag groups
    for theme_name, theme_data in themes.items():
        if not isinstance(theme_data, dict):
            continue
        groups = theme_data.get("tags_groups", {})
        if isinstance(groups, dict):
            theme_data["tags_groups"] = {
                gname: _normalise_group(gval) for gname, gval in groups.items()
            }

    _CACHE = themes
    return themes


def get_taxonomy() -> Dict[str, Any]:
    """Cached accessor used by route handlers."""
    return parse_taxonomy()
