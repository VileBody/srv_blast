"""Deterministic Stage2B — resolve an exact (theme, tags_group) bucket to a
FootageStyleRawPayload WITHOUT calling the LLM.

On the precision/vibe path the user picks an exact footage bucket, so Stage2B's
LLM was only COPYING fields out of footage_v2.py THEMES LOGIC:
  priority_theme_tags <- the group's tags
  exclude_tags        <- the group's _exclude_tags
  color_priority      <- the group's _color (or the theme color)
  exclude (people)    <- the theme's "exclude" list
All of these are already parsed structurally by `footage_bucket_catalog`, so the
copy is deterministic. We use ALL of the group's tags as priority_theme_tags
(the downstream picker scores by overlap count — more tags = broader/better than
the LLM's arbitrary 6-10 subset), then feed the result to the SAME deterministic
picker adapter the LLM rotation path uses (`resolve_style_pick_from_raw_filters`).

This module only builds the raw payload / rotation; genre+tag resolution against
the live inventory stays in footage_picker so both paths share one code path.
"""
from __future__ import annotations

from typing import List, Optional

from mlcore.footage_bucket_catalog import Bucket, build_buckets
from mlcore.models.footage_style import (
    FootageStyleRawFilters,
    FootageStyleRawPayload,
    FootageStyleRotation,
)


def _norm(v: object) -> str:
    return " ".join(str(v or "").strip().lower().split())


def find_bucket(
    theme: str,
    tags_group: str,
    *,
    catalog: Optional[List[Bucket]] = None,
) -> Bucket:
    """Find the exact (theme, tags_group) bucket.

    Looks up the NON-deduped catalog (`build_buckets`) so the exact slot the user
    chose is always found with its own theme-level exclusions — dedup collapses
    visual twins across themes and could otherwise hide the requested theme.
    """
    t = _norm(theme)
    g = _norm(tags_group)
    if not t or not g:
        raise RuntimeError(
            "deterministic Stage2B requires both theme and tags_group "
            f"(got theme={theme!r} tags_group={tags_group!r})"
        )
    if t == "visual":
        from mlcore.footage_visual_catalog import load_visual_catalog
        wanted = f"visual:{g}"
        for contract in load_visual_catalog():
            if contract.bucket_id == wanted:
                return contract  # type: ignore[return-value]
        raise RuntimeError(f"visual contract not found: {wanted!r}")
    buckets = catalog if catalog is not None else build_buckets()
    for b in buckets:
        if b.theme == t and b.tags_group == g:
            return b
    raise RuntimeError(
        f"deterministic Stage2B: bucket not found in catalog theme={t!r} tags_group={g!r}"
    )


def bucket_to_style_raw(bucket: Bucket) -> FootageStyleRawPayload:
    """Build a FootageStyleRawPayload from a catalog bucket (no LLM).

    Field validation (allowed colors / people / non-empty tags) is enforced by the
    pydantic models — an unexpected footage_v2 value surfaces as an explicit error
    instead of a silent fallback (No Fallback Policy).
    """
    is_visual = str(bucket.bucket_id).startswith("visual:")
    if not bucket.mood and not is_visual:
        raise RuntimeError(
            f"deterministic Stage2B: bucket {bucket.bucket_id!r} has no mood "
            "(theme must end in _major/_minor)"
        )
    people_mode = "any" if is_visual else str(getattr(bucket, "people", "any"))
    exclude_people = (
        ["girls", "guys", "couple", "crowd", "driver"] if people_mode == "none" else []
    )
    require_people = "girls" if people_mode == "girls" else None
    filters = FootageStyleRawFilters(
        color_priority=list(bucket.color) or ["dark", "light", "warm", "cold"],
        exclude=exclude_people or list(bucket.exclude),
        exclude_tags=[] if is_visual else list(bucket.exclude_tags),
        require_people=require_people,
        priority_theme_tags=list(bucket.priority_tags),
    )
    return FootageStyleRawPayload(
        artist_id=None,
        theme=bucket.theme,
        mood=bucket.mood or "minor",
        tags_group=bucket.tags_group,
        filters=filters,
    )


def resolve_style_raw(
    theme: str,
    tags_group: str,
    *,
    catalog: Optional[List[Bucket]] = None,
) -> FootageStyleRawPayload:
    """theme+tags_group -> FootageStyleRawPayload (deterministic, no LLM)."""
    return bucket_to_style_raw(find_bucket(theme, tags_group, catalog=catalog))


def resolve_style_rotation(
    theme: str,
    tags_group: str,
    *,
    catalog: Optional[List[Bucket]] = None,
) -> FootageStyleRotation:
    """Wrap the resolved bucket as a single-subgroup FootageStyleRotation, matching
    the ROTATION_OVERRIDE LLM path's output shape (exactly one subgroup on the
    requested theme/group) so the downstream resolution code is identical."""
    raw = resolve_style_raw(theme, tags_group, catalog=catalog)
    return FootageStyleRotation(subgroups=[raw])
