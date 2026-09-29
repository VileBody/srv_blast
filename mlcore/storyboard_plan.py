"""Storyboard plan: the render's own deterministic cuts and clip picks, computed
outside a job so the web wizard can show them, let the user swap clips, and then
send the exact result back to the render as a pinned ``footage_plan``.

Nothing here decides anything new. Every step is the function the render job
already calls on the exact-slot path (``gemini_orchestrator``):

* cuts   — ``generate_switch_points`` over the hook audio analysis, then the same
           ``normalize_switch_points`` / collection interval cap;
* bucket — ``resolve_style_rotation`` → ``resolve_style_pick_from_raw_filters``;
* clips  — ``pick_footage_clips_by_intervals_deterministic``.

Why a pinned plan at all: the picker reads a global cooldown ledger that moves with
every other render, so re-running it at render time would not reproduce what the
user saw. A plan freezes cuts + clips; the render then skips exactly those two
steps and nothing else.

Pace. The timeline offers «реже / авто / чаще». «Авто» is the render default
(``SwitchTimingParams()``); the other two only change the target beat spacing, so
they are still snapped to the same measured beats of this track.

No-fallback: a plan whose window does not cover the job window, or that names a
clip outside the slot's pool, too short for its shot or excluded by the operator
blacklist, is an explicit error — never a silent re-pick.
A job window narrower than the plan (subtitle phrase-snapping) only trims the
edge shots; see ``validate_plan``.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from mlcore import footage_picker as fp
from mlcore.models.footage_plan import FootageClipPick, FootageSelectionPayload
from mlcore.models.switch_timing import normalize_switch_points
from mlcore.switch_timing_deterministic import (
    SwitchTimingParams,
    enforce_max_interval,
    generate_switch_points,
)

PACES: Tuple[str, ...] = ("sparse", "auto", "dense")

_BASE = SwitchTimingParams()
PACE_PARAMS: Dict[str, SwitchTimingParams] = {
    # Half the cuts: twice the beat spacing, longer holds allowed.
    "sparse": replace(
        _BASE,
        drop_gap_beats=_BASE.drop_gap_beats * 2,
        drop_gap_floor_sec=_BASE.drop_gap_floor_sec * 1.6,
        default_gap_beats=_BASE.default_gap_beats * 2,
        default_gap_floor_sec=_BASE.default_gap_floor_sec * 1.6,
        max_hold_sec=_BASE.max_hold_sec * 1.6,
    ),
    "auto": _BASE,
    # Twice the cuts: half the beat spacing, shorter holds.
    "dense": replace(
        _BASE,
        drop_gap_beats=_BASE.drop_gap_beats / 2,
        drop_gap_floor_sec=_BASE.drop_gap_floor_sec * 0.6,
        default_gap_beats=_BASE.default_gap_beats / 2,
        default_gap_floor_sec=_BASE.default_gap_floor_sec * 0.55,
        max_hold_sec=_BASE.max_hold_sec * 0.6,
    ),
}

# Same normalisation the render applies to deterministic cuts.
_MERGE_GAP_SEC = 0.2
_MIN_SEGMENT_SEC = 0.3
_WINDOW_TOL_SEC = 1e-3


class StoryboardPlanError(RuntimeError):
    """A plan or request the render could not honour exactly."""


# ── cuts ──────────────────────────────────────────────────────────────────────

@dataclass
class CutsResult:
    clip_start_abs: float
    clip_end_abs: float
    bpm: float
    drop_t: Optional[float]
    beats_abs: List[float]
    cuts_by_pace: Dict[str, List[float]]


def effective_drop(analysis: Any, *, clip_start_abs: float, clip_end_abs: float, user_drop_t: Optional[float]) -> Optional[float]:
    """The drop the render pivots on: the user's pick when given (validated the
    same way as ``USER_DROP_T``), otherwise the analysis' top candidate."""
    if user_drop_t is not None:
        d = float(user_drop_t)
        if not (clip_start_abs <= d <= clip_end_abs):
            raise StoryboardPlanError(
                f"user_drop_t={d!r} outside clip window [{clip_start_abs}, {clip_end_abs}]"
            )
        return round(d, 3)
    cands = list(getattr(analysis, "drop_candidates", None) or [])
    return float(cands[0].t) if cands else None


def cuts_for_pace(
    analysis: Any,
    *,
    clip_start_abs: float,
    clip_end_abs: float,
    drop_t: Optional[float],
    pace: str = "auto",
    interval_cap_sec: float = 0.0,
) -> List[float]:
    if pace not in PACE_PARAMS:
        raise StoryboardPlanError(f"unknown pace {pace!r}; expected one of {PACES}")
    params = PACE_PARAMS[pace]
    if interval_cap_sec > 0.0 and interval_cap_sec < params.max_hold_sec:
        params = replace(params, max_hold_sec=interval_cap_sec)
    onsets = [(float(o.t), str(o.type), float(o.confidence)) for o in analysis.onsets_classified]
    det = generate_switch_points(
        onsets_classified=onsets,
        beats=[float(b) for b in analysis.beats],
        bpm=float(analysis.bpm) if analysis.bpm else 0.0,
        drop_t=drop_t,
        clip_start=clip_start_abs,
        clip_end=clip_end_abs,
        params=params,
    )
    points = normalize_switch_points(
        raw_cut_timings=list(det.switch_points_abs),
        clip_start_abs=clip_start_abs,
        clip_end_abs=clip_end_abs,
        merge_gap_sec=_MERGE_GAP_SEC,
        min_segment_sec=_MIN_SEGMENT_SEC,
        compact_short_segments=True,
    )
    if interval_cap_sec > 0.0:
        points = enforce_max_interval(
            points, clip_start=clip_start_abs, clip_end=clip_end_abs, max_interval_sec=interval_cap_sec,
        )
    return [round(float(p), 3) for p in points]


def compute_cuts(
    analysis: Any,
    *,
    clip_start_abs: float,
    clip_end_abs: float,
    user_drop_t: Optional[float] = None,
    interval_cap_sec: float = 0.0,
) -> CutsResult:
    drop_t = effective_drop(analysis, clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, user_drop_t=user_drop_t)
    cuts = {
        pace: cuts_for_pace(
            analysis,
            clip_start_abs=clip_start_abs,
            clip_end_abs=clip_end_abs,
            drop_t=drop_t,
            pace=pace,
            interval_cap_sec=interval_cap_sec,
        )
        for pace in PACES
    }
    beats = [round(float(b), 3) for b in analysis.beats if clip_start_abs <= float(b) <= clip_end_abs]
    return CutsResult(
        clip_start_abs=float(clip_start_abs),
        clip_end_abs=float(clip_end_abs),
        bpm=float(analysis.bpm or 0.0),
        drop_t=drop_t,
        beats_abs=beats,
        cuts_by_pace=cuts,
    )


# ── bucket + picks ─────────────────────────────────────────────────────────────

def slot_pool(subgroups: Sequence[Any], assets: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The clips one exact slot may use, by file_name: the same raw pool the picker
    builds per rotation subgroup. Both the storyboard and the render's plan check
    use it, so a pinned clip is valid in one exactly when it is valid in the other."""
    pool: List[Dict[str, Any]] = []
    for subgroup in subgroups:
        pool.extend(fp._build_raw_pool(subgroup, assets))
    return {str(it.get("file_name") or ""): it for it in fp._dedupe_assets_by_file_name(pool)}


@dataclass
class BucketContext:
    """Everything the picker needs about one exact slot (theme, tags_group)."""
    rotation: Any                       # FootageStyleRotation
    style_pick: Any                     # FootageStylePickPayload
    assets: List[Dict[str, Any]]        # inventory mapped to style metadata
    cooldown_by_name: Optional[Dict[str, float]] = None
    # operator blacklist (FOOTAGE_BLACKLIST_PATH / FOOTAGE_EXCLUDE_FILE_NAMES_JSON)
    excluded: FrozenSet[str] = frozenset()
    _pool: Optional[Dict[str, Dict[str, Any]]] = field(default=None, repr=False)

    @property
    def by_name(self) -> Dict[str, Dict[str, Any]]:
        return {str(a.get("file_name") or ""): a for a in self.assets}

    @property
    def pool(self) -> Dict[str, Dict[str, Any]]:
        if self._pool is None:
            self._pool = slot_pool(self.rotation.subgroups, self.assets)
        return self._pool


def resolve_bucket(
    *,
    theme: str,
    tags_group: str,
    mapped_assets: List[Dict[str, Any]],
    seed_key: str,
    total_assets: Optional[int] = None,
    cooldown_by_name: Optional[Dict[str, float]] = None,
    excluded_file_names: Iterable[str] = (),
) -> BucketContext:
    from mlcore.footage_style_resolver import resolve_style_rotation

    if not theme or not tags_group:
        raise StoryboardPlanError("storyboard needs an exact slot: theme and tags_group are both required")
    if not mapped_assets:
        raise StoryboardPlanError("no inventory assets are mapped to style metadata")
    rotation = resolve_style_rotation(theme, tags_group)
    style_pick, _diag = fp.resolve_style_pick_from_raw_filters(
        raw_pick=rotation.subgroups[0],
        mapped_assets=mapped_assets,
        seed_key=seed_key,
        total_assets=total_assets if total_assets is not None else len(mapped_assets),
    )
    return BucketContext(
        rotation=rotation,
        style_pick=style_pick,
        assets=mapped_assets,
        cooldown_by_name=cooldown_by_name,
        excluded=frozenset(str(n) for n in excluded_file_names if str(n)),
    )


@dataclass
class VideoRequest:
    seed_key: str
    # interval index -> pinned file_name (the user's replacement)
    pins: Dict[int, str] = field(default_factory=dict)


@dataclass
class VideoPick:
    seed_key: str
    selection: FootageSelectionPayload
    # interval indexes whose clip is also used by another video of the batch
    # (the bucket ran out of fresh clips — shown to the user, never hidden)
    repeats: List[int] = field(default_factory=list)


def _pinned_clip(clip: FootageClipPick, file_name: str) -> FootageClipPick:
    return FootageClipPick(
        file_name=file_name,
        fit_mode=clip.fit_mode,
        in_point=clip.in_point,
        out_point=clip.out_point,
        start_time=clip.in_point,
        source_offset_sec=0.0,
    )


def _check_clip(pool: Mapping[str, Dict[str, Any]], excluded: Iterable[str], file_name: str, *,
                interval_len: float, what: str) -> None:
    """One rule for a user-chosen clip, in the storyboard and in the render alike:
    it is in this slot's pool, it is not blacklisted, it covers its shot."""
    asset = pool.get(file_name)
    if asset is None:
        raise StoryboardPlanError(f"{what} {file_name!r} is not in the inventory of this slot")
    if file_name in set(excluded):
        raise StoryboardPlanError(f"{what} {file_name!r} is blacklisted")
    if not fp._fits_interval(asset, interval_len=interval_len):
        raise StoryboardPlanError(f"{what} {file_name!r} is shorter than its {interval_len:.2f}s shot")


def pick_batch(
    ctx: BucketContext,
    *,
    clip_start_abs: float,
    clip_end_abs: float,
    switch_points_abs: Sequence[float],
    videos: Sequence[VideoRequest],
) -> List[VideoPick]:
    """Pick every video of one bucket in order; clips already used by earlier
    videos are excluded so a batch does not repeat itself."""
    intervals = fp.build_intervals_from_switch_points(
        clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, switch_points_abs=list(switch_points_abs),
    )
    used: Dict[str, int] = {}
    out: List[VideoPick] = []
    for vi, video in enumerate(videos):
        for idx, name in video.pins.items():
            if not (0 <= idx < len(intervals)):
                raise StoryboardPlanError(f"pin index {idx} outside {len(intervals)} shots")
            a, b = intervals[idx]
            _check_clip(ctx.pool, ctx.excluded, name, interval_len=b - a, what="pinned clip")
        exclude = set(used) | set(video.pins.values()) | set(ctx.excluded)
        selection, _diag = fp.pick_footage_clips_by_intervals_deterministic(
            style_pick=ctx.style_pick,
            assets=ctx.assets,
            clip_start_abs=clip_start_abs,
            clip_end_abs=clip_end_abs,
            switch_points_abs=list(switch_points_abs),
            seed_key=video.seed_key,
            fit_mode="cover",
            exclude_file_names=sorted(exclude),
            raw_pick=None,
            raw_picks=ctx.rotation.subgroups,
            cooldown_by_name=ctx.cooldown_by_name,
        )
        clips = list(selection.clips)
        if len(clips) != len(intervals):
            raise StoryboardPlanError(
                f"picker returned {len(clips)} clips for {len(intervals)} shots"
            )
        for idx, name in video.pins.items():
            clips[idx] = _pinned_clip(clips[idx], name)
        repeats = [i for i, c in enumerate(clips) if c.file_name in used and used[c.file_name] != vi]
        for c in clips:
            used.setdefault(c.file_name, vi)
        out.append(VideoPick(
            seed_key=video.seed_key,
            selection=FootageSelectionPayload(clips=clips, allow_gaps=selection.allow_gaps),
            repeats=repeats,
        ))
    return out


def alternatives(
    ctx: BucketContext,
    *,
    clip_start_abs: float,
    clip_end_abs: float,
    switch_points_abs: Sequence[float],
    interval_idx: int,
    seed_key: str,
    exclude_file_names: Iterable[str] = (),
    limit: int = 24,
) -> List[str]:
    """Candidates for one shot, best first: the same pool, fit rule and ordering
    the picker uses for that interval, minus clips already on screen."""
    intervals = fp.build_intervals_from_switch_points(
        clip_start_abs=clip_start_abs, clip_end_abs=clip_end_abs, switch_points_abs=list(switch_points_abs),
    )
    if not (0 <= interval_idx < len(intervals)):
        raise StoryboardPlanError(f"shot index {interval_idx} outside {len(intervals)} shots")
    a, b = intervals[interval_idx]
    excluded = {str(x) for x in exclude_file_names} | set(ctx.excluded)
    pool = [
        it for name, it in ctx.pool.items()
        if name not in excluded and fp._fits_interval(it, interval_len=b - a)
    ]
    scores = {str(it.get("file_name")): float(it.get(fp._SELECTION_RANK_SCORE_KEY) or 0.0) for it in pool}
    ordered = fp._deterministic_file_name_order(
        file_names=list(scores),
        seed_value=fp.deterministic_seed_from_key(seed_key),
        interval_idx=interval_idx,
        interval_start=float(a),
        scores_by_name=scores,
        cooldown_by_name=ctx.cooldown_by_name,
    )
    return ordered[: max(0, int(limit))]


# ── the pinned plan the render consumes ────────────────────────────────────────

def build_plan(
    *,
    clip_start_abs: float,
    clip_end_abs: float,
    switch_points_abs: Sequence[float],
    selection: FootageSelectionPayload,
) -> Dict[str, Any]:
    return {
        "version": 1,
        "clip_start_abs": float(clip_start_abs),
        "clip_end_abs": float(clip_end_abs),
        "switch_points_abs": [float(p) for p in switch_points_abs],
        "clips": [c.model_dump() for c in selection.clips],
    }


def validate_cuts(
    cuts: Dict[str, Any],
    *,
    clip_start_abs: float,
    clip_end_abs: float,
) -> List[float]:
    """The timeline's cuts without clips (``pinned_cuts``): used for every video
    whose clips the render still picks itself — photo, collections, strobe, a
    vibe without a storyboard. Same window rule as ``validate_plan``: the job
    window may be narrower (cuts outside it are dropped), never wider."""
    if not isinstance(cuts, dict) or int(cuts.get("version") or 0) != 1:
        raise StoryboardPlanError("pinned_cuts: unsupported or missing version")
    ps, pe = float(cuts.get("clip_start_abs", -1)), float(cuts.get("clip_end_abs", -1))
    js, je = float(clip_start_abs), float(clip_end_abs)
    if js < ps - _WINDOW_TOL_SEC or je > pe + _WINDOW_TOL_SEC:
        raise StoryboardPlanError(
            f"pinned_cuts window {ps:.3f}..{pe:.3f} does not cover the job window {js:.3f}..{je:.3f}"
        )
    points = [float(p) for p in cuts.get("switch_points_abs") or []]
    if any(not (ps < p < pe) for p in points) or any(b <= a for a, b in zip(points, points[1:])):
        raise StoryboardPlanError("pinned_cuts: switch points must be increasing and inside their window")
    return [p for p in points if js + _WINDOW_TOL_SEC < p < je - _WINDOW_TOL_SEC]


def _trim_clip(clip: FootageClipPick, start: float, end: float) -> FootageClipPick:
    """Clamp a clip to [start, end] keeping the same source frames on screen:
    moving in_point forward by d plays the source d seconds later."""
    new_in = max(float(clip.in_point), start)
    new_out = min(float(clip.out_point), end)
    offset = float(clip.source_offset_sec) + (new_in - float(clip.in_point))
    return FootageClipPick(
        file_name=clip.file_name,
        fit_mode=clip.fit_mode,
        framing=clip.framing,
        in_point=new_in,
        out_point=new_out,
        start_time=new_in - offset,
        source_offset_sec=offset,
    )


def validate_plan(
    plan: Dict[str, Any],
    *,
    clip_start_abs: float,
    clip_end_abs: float,
    pool_by_name: Mapping[str, Dict[str, Any]],
    excluded_file_names: Iterable[str] = (),
) -> Tuple[List[float], FootageSelectionPayload]:
    """Check a pinned plan against the job it is applied to. Returns the switch
    points and the footage selection to use.

    Times in a plan are absolute on the track. Subtitle modes that build a
    ``SubtitleFlowPlan`` may snap the job window to phrase edges, so the job
    window can be NARROWER than the plan window: then the edge shots are trimmed
    (same frames, deterministic) and nothing inside changes. A job window that
    reaches OUTSIDE the plan would show footage the user never saw — that is an
    explicit error.

    The plan comes from the browser, so every clip is re-checked here with the
    storyboard's own rule (``_check_clip``): it belongs to this slot's pool
    (``slot_pool``), it is not blacklisted, and it is long enough for its shot.
    """
    if not isinstance(plan, dict) or int(plan.get("version") or 0) != 1:
        raise StoryboardPlanError("footage_plan: unsupported or missing version")
    ps, pe = float(plan.get("clip_start_abs", -1)), float(plan.get("clip_end_abs", -1))
    js, je = float(clip_start_abs), float(clip_end_abs)
    if js < ps - _WINDOW_TOL_SEC or je > pe + _WINDOW_TOL_SEC:
        raise StoryboardPlanError(
            f"footage_plan window {ps:.3f}..{pe:.3f} does not cover the job window {js:.3f}..{je:.3f}"
        )
    plan_points = [float(p) for p in plan.get("switch_points_abs") or []]
    plan_intervals = fp.build_intervals_from_switch_points(
        clip_start_abs=ps, clip_end_abs=pe, switch_points_abs=plan_points,
    )
    selection = FootageSelectionPayload.model_validate({"clips": plan.get("clips") or [], "allow_gaps": False})
    if len(selection.clips) != len(plan_intervals):
        raise StoryboardPlanError(
            f"footage_plan has {len(selection.clips)} clips for {len(plan_intervals)} shots"
        )
    excluded = {str(n) for n in excluded_file_names}
    for i, (clip, (a, b)) in enumerate(zip(selection.clips, plan_intervals)):
        if abs(clip.in_point - a) > _WINDOW_TOL_SEC or abs(clip.out_point - b) > _WINDOW_TOL_SEC:
            raise StoryboardPlanError(
                f"footage_plan clip {i} spans {clip.in_point:.3f}..{clip.out_point:.3f}, "
                f"shot is {a:.3f}..{b:.3f}"
            )
        _check_clip(pool_by_name, excluded, clip.file_name, interval_len=b - a, what=f"footage_plan clip {i}")

    # Trim to the job window (a no-op when the windows are equal).
    points = [p for p in plan_points if js + _WINDOW_TOL_SEC < p < je - _WINDOW_TOL_SEC]
    clips = [
        _trim_clip(c, js, je)
        for c in selection.clips
        if float(c.out_point) > js + _WINDOW_TOL_SEC and float(c.in_point) < je - _WINDOW_TOL_SEC
    ]
    return points, FootageSelectionPayload(clips=clips, allow_gaps=False)
