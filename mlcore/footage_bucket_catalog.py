"""Machine-readable footage bucket catalog — the rankable unit of the precision
picking flow.

A BUCKET = (theme, tags_group) with its priority tags. The user picks a bucket
(a visual vibe); internally it's a (theme, tags_group) exact-slot for Stage2B.
This module parses the THEMES LOGIC section of footage_v2.py (the prompt the LLM
sees) into structured buckets, so the ranker and the dedup don't depend on prose
parsing scattered around the codebase. footage_v2.py stays the single source;
this is a derived, in-memory view (a CI gate keeps them honest).

Dedup: buckets with the SAME priority-tag set produce identical footage (the
picker matches on tags, not theme), so visual twins (e.g. eerie_nature under
jealousy/loneliness/mysticism) collapse to one shortlist entry.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.styles.theme_groups import get_subtheme_label, get_theme_label

_FOOTAGE_V2 = Path(__file__).resolve().parents[1] / "footage_v2.py"


def _norm(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


def parse_themes_logic(src: str) -> Dict[str, Any]:
    """Extract the THEMES LOGIC block from footage_v2.py source as a dict.

    The block is valid JSON once isolated and wrapped in braces.
    """
    i = src.find("THEMES LOGIC")
    if i < 0:
        raise RuntimeError("THEMES LOGIC section not found in footage_v2.py")
    body = src[i:]
    m = re.search(r'\n\s*("[a-z0-9_]+_(?:minor|major)":\s*\{)', body)
    if not m:
        raise RuntimeError("No theme entries found under THEMES LOGIC")
    end = body.find("STEP 5")
    chunk = body[m.start():end if end > 0 else len(body)].strip()
    chunk = re.sub(r"\n=+\s*$", "", chunk).strip().rstrip("=").strip()
    try:
        return json.loads("{" + chunk + "}")
    except Exception as e:
        raise RuntimeError(f"THEMES LOGIC is not valid JSON after extraction: {e}") from e


def _mood_of_theme(theme: str) -> str:
    t = str(theme or "")
    if t.endswith("_major"):
        return "major"
    if t.endswith("_minor"):
        return "minor"
    return ""


def _group_fields(group_value: Any, theme_color: List[str]) -> Dict[str, Any]:
    """Normalize a tags_group value (bare list OR {_tags,_exclude_tags,_color})."""
    if isinstance(group_value, list):
        tags = group_value
        excl: List[Any] = []
        color = theme_color
    elif isinstance(group_value, dict):
        tags = group_value.get("_tags") or []
        excl = group_value.get("_exclude_tags") or []
        color = group_value.get("_color") or theme_color
    else:
        tags, excl, color = [], [], theme_color
    return {
        "priority_tags": [t for t in (_norm(x) for x in tags) if t],
        "exclude_tags": [t for t in (_norm(x) for x in excl) if t],
        "color": [c for c in (_norm(x) for x in color) if c],
    }


@dataclass(frozen=True)
class Bucket:
    bucket_id: str          # "theme:tags_group"
    theme: str
    tags_group: str
    mood: str               # major|minor|""
    priority_tags: List[str] = field(default_factory=list)
    exclude_tags: List[str] = field(default_factory=list)
    color: List[str] = field(default_factory=list)
    theme_label: str = ""
    subtheme_label: str = ""

    @property
    def label(self) -> str:
        return self.subtheme_label or self.tags_group

    @property
    def tag_key(self) -> frozenset:
        return frozenset(self.priority_tags)


def build_buckets(src: Optional[str] = None) -> List[Bucket]:
    """All (theme, tags_group) buckets, NOT deduped, in source order."""
    if src is None:
        src = _FOOTAGE_V2.read_text(encoding="utf-8")
    logic = parse_themes_logic(src)
    out: List[Bucket] = []
    for theme, tv in logic.items():
        if not isinstance(tv, dict):
            continue
        theme_color = [c for c in (_norm(x) for x in (tv.get("color") or [])) if c]
        groups = tv.get("tags_groups") or {}
        for group, gv in groups.items():
            f = _group_fields(gv, theme_color)
            if not f["priority_tags"]:
                continue
            out.append(Bucket(
                bucket_id=f"{theme}:{group}",
                theme=theme,
                tags_group=group,
                mood=_mood_of_theme(theme),
                priority_tags=f["priority_tags"],
                exclude_tags=f["exclude_tags"],
                color=f["color"],
                theme_label=get_theme_label(theme),
                subtheme_label=get_subtheme_label(group),
            ))
    return out


def dedup_buckets(buckets: List[Bucket]) -> List[Bucket]:
    """Collapse visual twins (identical priority-tag set), keeping the first."""
    seen: set = set()
    out: List[Bucket] = []
    for b in buckets:
        key = (b.tag_key, b.mood)
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def get_bucket_catalog(src: Optional[str] = None) -> List[Bucket]:
    """Deduped, rankable bucket catalog."""
    return dedup_buckets(build_buckets(src))
