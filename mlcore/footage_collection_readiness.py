"""Can this collection actually serve a job? Answer it at ingest, not at render.

The picker's failures for an under-sized pool are correct but cryptic, and they
surface to a paying user as a failed render rather than to an operator as a
warning:

    insufficient unique assets for strict no-repeat policy: need=64 have=41
    no asset can cover interval for strict no-repeat policy: idx=7 ... dur=3.412

Both are decided by facts known the moment a folder is indexed — how many clips
it has and how long they are — so they can be reported while the operator is
still looking at the upload.

The demand numbers come from the deterministic switch timing (the only mode prod
runs), not from guesses: cuts land about `default_gap_beats` apart with a floor
of `default_gap_floor_sec`, and no shot is ever held longer than `max_hold_sec`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from mlcore.switch_timing_deterministic import SwitchTimingParams

_P = SwitchTimingParams()

# The longest interval the timing can ask a single clip to cover. A clip shorter
# than this cannot fill such an interval, and the strict no-repeat matcher has no
# way to substitute a second clip for it.
MAX_INTERVAL_SEC: float = float(_P.max_hold_sec)

# Cuts outside the drop window sit `default_gap_beats` apart but never tighter
# than the floor, so the floor is what bounds the WORST case (fastest track =
# most cuts = most unique clips needed).
MIN_GAP_SEC: float = float(_P.default_gap_floor_sec)

# Longest window a user can pick, in seconds. The reel formats top out here, and
# a collection that covers this covers everything shorter.
DEFAULT_WINDOW_SEC: float = 60.0


def clips_needed_for_window(window_sec: float = DEFAULT_WINDOW_SEC) -> int:
    """Unique clips a job of this length can demand in the worst case.

    Every interval needs its OWN clip — the no-repeat policy is strict — so the
    count is the number of intervals, not a fraction of it.
    """
    return max(1, int(float(window_sec) / MIN_GAP_SEC) + 1)


@dataclass(frozen=True)
class CollectionReadiness:
    slug: str
    clips: int
    min_duration_sec: float
    median_duration_sec: float
    clips_covering_longest_interval: int
    needed_clips: int

    @property
    def can_fill_longest_interval(self) -> bool:
        return self.clips_covering_longest_interval > 0

    @property
    def has_enough_clips(self) -> bool:
        return self.clips >= self.needed_clips

    @property
    def status(self) -> str:
        if not self.can_fill_longest_interval:
            return "unusable"
        if not self.has_enough_clips:
            return "thin"
        return "ok"

    @property
    def note(self) -> str:
        if self.status == "unusable":
            return (
                f"no clip reaches {MAX_INTERVAL_SEC:.1f}s, so the longest interval "
                f"cannot be filled (longest clip {self.min_duration_sec:.1f}s+); "
                "jobs will fail at selection"
            )
        if self.status == "thin":
            return (
                f"{self.clips} clips for up to {self.needed_clips} intervals in a "
                f"{DEFAULT_WINDOW_SEC:.0f}s reel — a fast track will exhaust the "
                "no-repeat pool"
            )
        return ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "clips": self.clips,
            "min_duration_sec": round(self.min_duration_sec, 2),
            "median_duration_sec": round(self.median_duration_sec, 2),
            "clips_covering_longest_interval": self.clips_covering_longest_interval,
            "needed_clips": self.needed_clips,
            "status": self.status,
        }


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2.0


def evaluate_collection(
    slug: str,
    durations: Iterable[float],
    *,
    window_sec: float = DEFAULT_WINDOW_SEC,
) -> CollectionReadiness:
    values = []
    for d in durations:
        try:
            v = float(d)
        except (TypeError, ValueError):
            continue
        if v > 0:
            values.append(v)
    return CollectionReadiness(
        slug=str(slug),
        clips=len(values),
        min_duration_sec=min(values) if values else 0.0,
        median_duration_sec=_median(values),
        clips_covering_longest_interval=sum(1 for v in values if v >= MAX_INTERVAL_SEC),
        needed_clips=clips_needed_for_window(window_sec),
    )


def evaluate_index(
    assets: Iterable[Mapping[str, Any]],
    *,
    window_sec: float = DEFAULT_WINDOW_SEC,
) -> List[CollectionReadiness]:
    """One readiness row per (kind, folder) present in a collection index."""
    by_slug: Dict[str, List[float]] = {}
    for row in assets or []:
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("genre") or "").strip()
        folder = str(row.get("tag") or "").strip()
        if not kind or not folder:
            continue
        by_slug.setdefault(f"{kind}__{folder}", []).append(row.get("duration_sec"))
    return [
        evaluate_collection(slug, durations, window_sec=window_sec)
        for slug, durations in sorted(by_slug.items())
    ]
