"""Distribute a batch of N videos over the user-selected footage buckets.

Phase 3 of the precision picking flow. Pure + deterministic so the bot enqueue
(phase 2b) just calls it. The user multi-selects 1+ buckets (ordered by rank);
each of the N batch videos is assigned one bucket:

  video[i] = selected[i % K]

This single rule covers every case the product wants:
  - 1 selected, N videos      -> N takes of the same vibe (variety via per-take
                                  seed + exclude scoped to the bucket — done bot-side).
  - K selected, N == K        -> exactly the chosen vibes, one each.
  - K selected, N > K          -> cycle (videos beyond K "inherit" earlier vibes).
  - K selected, N < K          -> the top-N selected (highest-ranked) are used.

The unit returned is the bucket_id ("theme:tags_group"); resolve_bucket_slot()
maps it to the (theme, tags_group) exact-slot the enqueue passes as
rotation_theme / rotation_tags_group.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from mlcore.footage_bucket_catalog import Bucket, get_bucket_catalog


def distribute_buckets(selected_bucket_ids: List[str], n_videos: int) -> List[str]:
    """Assign a bucket_id to each of the N videos (round-robin by rank order).

    `selected_bucket_ids` must be non-empty and ordered by preference/rank.
    Returns a list of length max(0, n_videos).
    """
    selected = [str(b).strip() for b in (selected_bucket_ids or []) if str(b).strip()]
    if not selected:
        raise ValueError("distribute_buckets requires at least one selected bucket")
    n = int(n_videos or 0)
    if n <= 0:
        return []
    k = len(selected)
    return [selected[i % k] for i in range(n)]


def resolve_bucket_slot(
    bucket_id: str, *, catalog: Optional[List[Bucket]] = None
) -> Tuple[str, str]:
    """Map a bucket_id -> (theme, tags_group) for the Stage2B exact-slot override.

    Falls back to splitting the id on ':' if it's not in the catalog (the id is
    constructed as f"{theme}:{tags_group}"), so callers stay robust.
    """
    bid = str(bucket_id or "").strip()
    cat = catalog if catalog is not None else get_bucket_catalog()
    for b in cat:
        if b.bucket_id == bid:
            return b.theme, b.tags_group
    if ":" in bid:
        theme, group = bid.split(":", 1)
        return theme.strip(), group.strip()
    raise ValueError(f"unknown bucket_id: {bucket_id!r}")


def distribute_slots(
    selected_bucket_ids: List[str], n_videos: int, *, catalog: Optional[List[Bucket]] = None
) -> List[Tuple[str, str]]:
    """Convenience: per-video (theme, tags_group) for the whole batch."""
    cat = catalog if catalog is not None else get_bucket_catalog()
    return [resolve_bucket_slot(bid, catalog=cat) for bid in distribute_buckets(selected_bucket_ids, n_videos)]
