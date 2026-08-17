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

# The longest hold the generator would choose on its own.
MAX_INTERVAL_SEC: float = float(_P.max_hold_sec)

# Cuts outside the drop window sit `default_gap_beats` apart but never tighter
# than the floor, so the floor is what bounds the WORST case (fastest track =
# most cuts = most unique clips needed).
MIN_GAP_SEC: float = float(_P.default_gap_floor_sec)

# Two cuts are never closer than this. An interval cap below it would mean cuts
# faster than the renderer will place them — the pool is genuinely unusable then,
# not merely demanding.
HARD_FLOOR_SEC: float = float(_P.hard_floor_sec)

# A clip exactly as long as its interval is a rounding error away from not
# fitting, so the cap leaves this much slack. Mirrors the orchestrator.
CAP_SLACK_SEC: float = 0.05

# Longest window a user can pick, in seconds. The reel formats top out here, and
# a collection that covers this covers everything shorter.
DEFAULT_WINDOW_SEC: float = 60.0


def interval_cap_for(min_duration_sec: float) -> float:
    """The longest interval a pool of these clips can actually cover.

    Collection jobs lower the generator's `max_hold_sec` to this, so a pre-cut
    batch does not have to be re-cut just because the music left a quiet stretch
    — the cuts land sooner instead of asking for a clip that does not exist.
    """
    return max(0.0, float(min_duration_sec) - CAP_SLACK_SEC)


def clips_needed_for_window(
    window_sec: float = DEFAULT_WINDOW_SEC,
    *,
    interval_cap_sec: float = 0.0,
) -> int:
    """Unique clips a job of this length can demand in the worst case.

    Every interval needs its OWN clip — the no-repeat policy is strict — so the
    count is the number of intervals, not a fraction of it. Cuts normally land a
    gap apart; a cap TIGHTER than that gap makes them land more often, and the
    demand rises with it.
    """
    spacing = MIN_GAP_SEC
    if interval_cap_sec and interval_cap_sec < spacing:
        spacing = float(interval_cap_sec)
    if spacing <= 0:
        return 1
    return max(1, int(float(window_sec) / spacing) + 1)


@dataclass(frozen=True)
class CollectionReadiness:
    slug: str
    clips: int
    min_duration_sec: float
    median_duration_sec: float
    interval_cap_sec: float
    needed_clips: int

    @property
    def cuts_are_watchable(self) -> bool:
        """A cap under the renderer's own floor means cuts faster than it places
        them — no amount of clips rescues that."""
        return self.interval_cap_sec >= HARD_FLOOR_SEC

    @property
    def has_enough_clips(self) -> bool:
        return self.clips >= self.needed_clips

    @property
    def status(self) -> str:
        if self.clips <= 0 or not self.cuts_are_watchable:
            return "unusable"
        if not self.has_enough_clips:
            return "thin"
        return "ok"

    @property
    def note(self) -> str:
        if self.status == "unusable":
            if self.clips <= 0:
                return "no clips with a measured duration — has the base been activated?"
            return (
                f"shortest clip {self.min_duration_sec:.2f}s forces cuts closer than "
                f"the {HARD_FLOOR_SEC:.1f}s floor; these clips are too short to build "
                "a watchable montage from"
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
            "interval_cap_sec": round(self.interval_cap_sec, 2),
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
    shortest = min(values) if values else 0.0
    cap = interval_cap_for(shortest)
    return CollectionReadiness(
        slug=str(slug),
        clips=len(values),
        min_duration_sec=shortest,
        median_duration_sec=_median(values),
        interval_cap_sec=cap,
        needed_clips=clips_needed_for_window(window_sec, interval_cap_sec=cap),
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
