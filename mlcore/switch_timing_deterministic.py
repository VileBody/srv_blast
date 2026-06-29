"""Deterministic footage cut-timing generator (kick-driven, rhythm-locked).

Replaces the two Stage2 LLM calls (timing analysis + cuts) with a transparent
algorithm over the measured audio features from `mlcore.audio_analysis`. The
LLM was only loosely following the "cut on the lows" intent; this makes the
core deterministic.

Rules (see SwitchTimingParams for the tunable numbers):
  * Cuts ride a tempo-relative grid. Each step targets `last + gap`.
  * Anchor priority at each step: kick (the sub-bass accent) → snare → (don't
    invent: jump to the next real kick; a long hold beats a synthetic cut).
  * gap is expressed in BEATS with a hard-seconds floor, so spacing is musical:
      default window   -> max(default_gap_beats * 60/bpm, default_gap_floor_sec)
      drop window (3s) -> max(drop_gap_beats   * 60/bpm, drop_gap_floor_sec)
    The drop window [drop, drop+3s] is slightly denser; after it we return to
    the default. We never go below ~0.8-1.0s even on fast tracks.
  * Rhythm: at each step pick the kick NEAREST the target gap (steady pulse,
    not ragged), then snap the chosen time to the nearest beat (±tol).
  * Hard floor: no two cuts closer than `hard_floor_sec`.

All times are absolute seconds on the full-track timeline (same as the rest of
the pipeline). The output is a sorted, de-duplicated list of switch points
inside (clip_start, clip_end); downstream `normalize_switch_points` still runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple


# A classified onset as the generator consumes it: (t_abs, type, confidence).
ClassifiedOnset = Tuple[float, str, float]


@dataclass
class SwitchTimingParams:
    drop_window_sec: float = 3.0          # meat-grinder window length after the drop
    drop_gap_beats: float = 1.0           # target spacing in the drop window (beats)
    drop_gap_floor_sec: float = 0.9       # but never tighter than this (user: 0.8-1.0)
    default_gap_beats: float = 2.0        # target spacing elsewhere (beats)
    default_gap_floor_sec: float = 1.6    # calmer floor outside the drop (~1.8-2.0s effective)
    max_hold_sec: float = 3.5             # never hold one shot longer than this
    beat_snap_tol_sec: float = 0.08       # snap a chosen cut to a beat within this
    # Anchor priority: kick (true sub-bass accent) → body (bass / low — adds
    # density when kicks are sparse) → snare. A "kick" labelled at low
    # confidence is still a low-frequency hit, so we DON'T apply the harsh
    # dominance-ratio confidence filter the LLM prompt used (it threw away most
    # usable kicks). min_conf=0.0 means "trust the dominant-band label".
    anchor_priority: Tuple[str, ...] = ("kick", "body", "snare")
    low_types: Tuple[str, ...] = ("kick", "body")   # used for the "don't invent" fallback
    min_conf: float = 0.0                 # rely on band classification, not ratio
    hard_floor_sec: float = 0.3           # absolute minimum between any two cuts
    force_cut_on_drop: bool = True        # always place a hard cut EXACTLY on the drop
    search_back_frac: float = 0.4         # search window before the target (× gap)
    search_fwd_frac: float = 0.6          # search window after the target (× gap)
    fallback_bpm: float = 120.0           # used if bpm is missing/invalid


@dataclass
class SwitchTimingResult:
    switch_points_abs: List[float]
    bpm: float
    drop_t: Optional[float]
    # Per-cut provenance, useful for logs/debug: "kick" | "snare" | "kick_far".
    sources: List[str] = field(default_factory=list)


def _onsets_of_type(
    onsets: Sequence[ClassifiedOnset], kind: str, *, min_conf: float,
    lo: float, hi: float,
) -> List[float]:
    out = [
        float(t)
        for (t, typ, c) in onsets
        if str(typ) == kind and float(c) >= min_conf and lo <= float(t) <= hi
    ]
    out.sort()
    return out


def _nearest_in_window(cands: Sequence[float], target: float, lo: float, hi: float) -> Optional[float]:
    best: Optional[float] = None
    best_d = 1e18
    for t in cands:
        if t < lo or t > hi:
            continue
        d = abs(t - target)
        if d < best_d:
            best_d = d
            best = t
    return best


def generate_switch_points(
    *,
    onsets_classified: Sequence[ClassifiedOnset],
    beats: Sequence[float],
    bpm: float,
    drop_t: Optional[float],
    clip_start: float,
    clip_end: float,
    params: Optional[SwitchTimingParams] = None,
) -> SwitchTimingResult:
    p = params or SwitchTimingParams()
    clip_start = float(clip_start)
    clip_end = float(clip_end)
    if clip_end <= clip_start:
        return SwitchTimingResult([], float(bpm or p.fallback_bpm), drop_t)

    use_bpm = float(bpm) if (bpm and float(bpm) > 0.0) else p.fallback_bpm
    beat_sec = 60.0 / use_bpm

    by_type = {
        typ: _onsets_of_type(onsets_classified, typ, min_conf=p.min_conf, lo=clip_start, hi=clip_end)
        for typ in set(p.anchor_priority) | set(p.low_types)
    }
    lows = sorted(t for typ in p.low_types for t in by_type.get(typ, []))
    beats_in = sorted(float(b) for b in beats if clip_start <= float(b) <= clip_end)

    drop = float(drop_t) if drop_t is not None else None

    def _in_drop(t: float) -> bool:
        return drop is not None and drop <= t < drop + p.drop_window_sec

    def floor_at(t: float) -> float:
        # the hard minimum gap for this window (never go below it, even when
        # snapping or allowing a little back-tolerance toward a strong kick)
        return p.drop_gap_floor_sec if _in_drop(t) else p.default_gap_floor_sec

    def gap_at(t: float) -> float:
        if _in_drop(t):
            return max(p.drop_gap_beats * beat_sec, p.drop_gap_floor_sec)
        return max(p.default_gap_beats * beat_sec, p.default_gap_floor_sec)

    def snap_beat(t: float) -> float:
        if not beats_in:
            return t
        b = min(beats_in, key=lambda x: abs(x - t))
        return b if abs(b - t) <= p.beat_snap_tol_sec else t

    cuts: List[float] = []
    sources: List[str] = []
    last = clip_start
    guard = 0
    while last < clip_end and guard < 100000:
        guard += 1
        g = gap_at(last)
        fl = floor_at(last)
        # Forced drop anchor: when the drop is within the next step's reach,
        # snap the cut EXACTLY onto it (a hard footage cut on the drop, not
        # beat-snapped) and resume the walk from there with drop-window spacing.
        if (
            p.force_cut_on_drop
            and drop is not None
            and last + p.hard_floor_sec <= drop < clip_end
            and drop <= last + g + p.search_fwd_frac * g
        ):
            cuts.append(round(float(drop), 3))
            sources.append("drop")
            last = float(drop)
            continue
        target = last + g
        if target >= clip_end:
            break
        # back-tolerance toward a strong kick, but NEVER below the window floor
        # (drop must stay >= ~0.8-1.0s, not slip to 0.6-0.7s).
        lo = max(target - p.search_back_frac * g, last + fl)
        hi = target + p.search_fwd_frac * g

        pick = None
        src = ""
        for typ in p.anchor_priority:
            pick = _nearest_in_window(by_type.get(typ, []), target, lo, hi)
            if pick is not None:
                src = typ
                break
        if pick is None:
            # No low/snare near the target. Don't hold static longer than
            # max_hold: place a cut on the nearest BEAT inside
            # [last+floor, last+max_hold] (on-tempo, not an invented off-beat
            # cut). Beats are ~every 60/bpm, so this caps holds without mush.
            pick = _nearest_in_window(
                beats_in, target, last + fl, last + p.max_hold_sec
            )
            src = "beat"
        if pick is None:
            # No beats either (degenerate) — jump to the next real low rather
            # than invent a time.
            nxt = next((t for t in lows if t > target), None)
            if nxt is None or nxt >= clip_end:
                break
            pick, src = nxt, "low_far"

        snapped = snap_beat(pick)
        if snapped - last < p.hard_floor_sec:
            # too close after snapping — advance without emitting to avoid a stutter
            last = max(snapped, last + p.hard_floor_sec)
            continue
        cuts.append(round(snapped, 3))
        sources.append(src)
        last = snapped

    # de-dup while keeping source of first occurrence
    seen: set[float] = set()
    out_pts: List[float] = []
    out_src: List[str] = []
    for t, s in zip(cuts, sources):
        if t in seen or not (clip_start < t < clip_end):
            continue
        seen.add(t)
        out_pts.append(t)
        out_src.append(s)
    return SwitchTimingResult(out_pts, use_bpm, drop, out_src)
