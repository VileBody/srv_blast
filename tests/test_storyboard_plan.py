"""Storyboard plan: the render's deterministic cuts/picks computed for the web
wizard, plus the pinned ``footage_plan`` contract the render consumes."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from mlcore import storyboard_plan as sp
from mlcore.footage_style_resolver import resolve_style_raw
from mlcore.models.switch_timing import normalize_switch_points
from mlcore.switch_timing_deterministic import generate_switch_points

THEME, GROUP = "romance_major", "nature_sunset"


def _analysis(*, bpm=120.0, length=20.0, drop=None):
    beats = [round(0.5 * i, 3) for i in range(int(length * 2) + 1)]
    onsets = [SimpleNamespace(t=b, type="kick", confidence=0.5) for b in beats[1:]]
    drops = [SimpleNamespace(t=drop)] if drop is not None else []
    return SimpleNamespace(bpm=bpm, beats=beats, onsets_classified=onsets, drop_candidates=drops)


def _assets(n: int, *, duration=6.0):
    tags = list(resolve_style_raw(THEME, GROUP).filters.priority_theme_tags)[:3]
    return [
        {
            "file_name": f"{3000 + i}.mp4",
            "genre": "g",
            "tag": "t",
            "duration_sec": duration,
            "src_w": 720,
            "src_h": 1280,
            "meta_theme_tags": tags,
            "meta_mood": "major",
            "meta_color_tone": "warm",
            "meta_people_type": "girls",
        }
        for i in range(n)
    ]


def _ctx(n=40, **kw):
    return sp.resolve_bucket(theme=THEME, tags_group=GROUP, mapped_assets=_assets(n, **kw), seed_key="batch-1")


# ── cuts ──────────────────────────────────────────────────────────────────────

def test_auto_pace_is_exactly_the_render_default():
    an = _analysis(drop=8.0)
    res = sp.compute_cuts(an, clip_start_abs=0.0, clip_end_abs=20.0)
    onsets = [(o.t, o.type, o.confidence) for o in an.onsets_classified]
    det = generate_switch_points(onsets_classified=onsets, beats=an.beats, bpm=120.0, drop_t=8.0, clip_start=0.0, clip_end=20.0)
    expected = normalize_switch_points(
        raw_cut_timings=det.switch_points_abs, clip_start_abs=0.0, clip_end_abs=20.0,
        merge_gap_sec=0.2, min_segment_sec=0.3, compact_short_segments=True,
    )
    assert res.cuts_by_pace["auto"] == [round(p, 3) for p in expected]


def test_paces_scale_the_cut_count_around_auto():
    res = sp.compute_cuts(_analysis(), clip_start_abs=0.0, clip_end_abs=20.0)
    sparse, auto, dense = (len(res.cuts_by_pace[p]) for p in ("sparse", "auto", "dense"))
    assert sparse < auto < dense


def test_pace_cuts_stay_on_the_tracks_beats():
    res = sp.compute_cuts(_analysis(), clip_start_abs=0.0, clip_end_abs=20.0)
    beats = set(res.beats_abs)
    for pace in sp.PACES:
        assert all(round(p, 3) in beats for p in res.cuts_by_pace[pace])


def test_user_drop_overrides_the_analysis_drop():
    res = sp.compute_cuts(_analysis(drop=4.0), clip_start_abs=0.0, clip_end_abs=20.0, user_drop_t=9.0)
    assert res.drop_t == 9.0
    assert 9.0 in res.cuts_by_pace["auto"]  # the render forces a cut on the drop


def test_user_drop_outside_window_is_an_error():
    with pytest.raises(sp.StoryboardPlanError):
        sp.compute_cuts(_analysis(), clip_start_abs=0.0, clip_end_abs=20.0, user_drop_t=25.0)


def test_unknown_pace_is_an_error():
    with pytest.raises(sp.StoryboardPlanError):
        sp.cuts_for_pace(_analysis(), clip_start_abs=0.0, clip_end_abs=20.0, drop_t=None, pace="turbo")


# ── picks ─────────────────────────────────────────────────────────────────────

CUTS = [2.0, 4.0, 6.0, 8.0]


def test_batch_videos_do_not_share_clips():
    picks = sp.pick_batch(
        _ctx(40), clip_start_abs=0.0, clip_end_abs=10.0, switch_points_abs=CUTS,
        videos=[sp.VideoRequest(seed_key=f"v{i}") for i in range(4)],
    )
    names = [c.file_name for p in picks for c in p.selection.clips]
    assert len(names) == 20 and len(set(names)) == 20
    assert all(not p.repeats for p in picks)


def test_batch_pick_is_deterministic():
    kw = dict(clip_start_abs=0.0, clip_end_abs=10.0, switch_points_abs=CUTS,
              videos=[sp.VideoRequest(seed_key="v0"), sp.VideoRequest(seed_key="v1")])
    one = [[c.file_name for c in p.selection.clips] for p in sp.pick_batch(_ctx(40), **kw)]
    two = [[c.file_name for c in p.selection.clips] for p in sp.pick_batch(_ctx(40), **kw)]
    assert one == two


def test_pin_replaces_exactly_that_shot():
    ctx = _ctx(40)
    base = sp.pick_batch(ctx, clip_start_abs=0.0, clip_end_abs=10.0, switch_points_abs=CUTS,
                         videos=[sp.VideoRequest(seed_key="v0")])[0].selection.clips
    free = next(a["file_name"] for a in ctx.assets if a["file_name"] not in {c.file_name for c in base})
    pinned = sp.pick_batch(ctx, clip_start_abs=0.0, clip_end_abs=10.0, switch_points_abs=CUTS,
                           videos=[sp.VideoRequest(seed_key="v0", pins={2: free})])[0].selection.clips
    assert pinned[2].file_name == free
    assert (pinned[2].in_point, pinned[2].out_point) == (base[2].in_point, base[2].out_point)
    assert free not in [c.file_name for i, c in enumerate(pinned) if i != 2]


def test_small_bucket_reports_repeats_instead_of_hiding_them():
    picks = sp.pick_batch(
        _ctx(6), clip_start_abs=0.0, clip_end_abs=10.0, switch_points_abs=CUTS,
        videos=[sp.VideoRequest(seed_key="v0"), sp.VideoRequest(seed_key="v1")],
    )
    assert picks[1].repeats  # 6 clips cannot fill 2 × 5 shots without reuse


def test_pin_outside_inventory_is_an_error():
    with pytest.raises(sp.StoryboardPlanError):
        sp.pick_batch(_ctx(40), clip_start_abs=0.0, clip_end_abs=10.0, switch_points_abs=CUTS,
                      videos=[sp.VideoRequest(seed_key="v0", pins={0: "nope.mp4"})])


def test_pin_shorter_than_its_shot_is_an_error():
    ctx = _ctx(10)
    ctx.assets[0]["duration_sec"] = 1.0
    with pytest.raises(sp.StoryboardPlanError):
        sp.pick_batch(ctx, clip_start_abs=0.0, clip_end_abs=10.0, switch_points_abs=CUTS,
                      videos=[sp.VideoRequest(seed_key="v0", pins={1: ctx.assets[0]["file_name"]})])


def test_alternatives_skip_clips_on_screen_and_fit_the_shot():
    ctx = _ctx(20)
    ctx.assets[5]["duration_sec"] = 1.0
    on_screen = [ctx.assets[0]["file_name"], ctx.assets[1]["file_name"]]
    alts = sp.alternatives(ctx, clip_start_abs=0.0, clip_end_abs=10.0, switch_points_abs=CUTS,
                           interval_idx=1, seed_key="v0", exclude_file_names=on_screen, limit=50)
    assert alts and not set(alts) & set(on_screen)
    assert ctx.assets[5]["file_name"] not in alts


# ── the pinned plan ───────────────────────────────────────────────────────────

def _plan(ctx):
    sel = sp.pick_batch(ctx, clip_start_abs=10.0, clip_end_abs=20.0, switch_points_abs=[12.0, 15.0, 17.5],
                        videos=[sp.VideoRequest(seed_key="v0")])[0].selection
    return sp.build_plan(clip_start_abs=10.0, clip_end_abs=20.0, switch_points_abs=[12.0, 15.0, 17.5], selection=sel)


def test_plan_round_trips_through_validation():
    ctx = _ctx(20)
    plan = _plan(ctx)
    points, sel = sp.validate_plan(plan, clip_start_abs=10.0, clip_end_abs=20.0, known_file_names=ctx.by_name)
    assert points == [12.0, 15.0, 17.5]
    assert [c.file_name for c in sel.clips] == [c["file_name"] for c in plan["clips"]]


def test_narrower_job_window_trims_only_the_edge_shots():
    """Subtitle phrase-snapping can narrow the job window: the edge shots are
    trimmed with the same frames on screen, inner shots stay untouched."""
    ctx = _ctx(20)
    plan = _plan(ctx)
    points, sel = sp.validate_plan(plan, clip_start_abs=11.0, clip_end_abs=19.0, known_file_names=ctx.by_name)
    assert points == [12.0, 15.0, 17.5]
    first, last = sel.clips[0], sel.clips[-1]
    assert (first.in_point, first.out_point) == (11.0, 12.0)
    assert first.source_offset_sec == pytest.approx(plan["clips"][0]["source_offset_sec"] + 1.0)
    assert first.start_time == pytest.approx(first.in_point - first.source_offset_sec)
    assert (last.in_point, last.out_point) == (17.5, 19.0)
    assert [c.file_name for c in sel.clips] == [c["file_name"] for c in plan["clips"]]


def test_trim_that_removes_a_whole_shot_drops_its_cut():
    ctx = _ctx(20)
    points, sel = sp.validate_plan(_plan(ctx), clip_start_abs=12.5, clip_end_abs=20.0, known_file_names=ctx.by_name)
    assert points == [15.0, 17.5] and len(sel.clips) == 3


def test_job_window_wider_than_the_plan_is_rejected():
    ctx = _ctx(20)
    with pytest.raises(sp.StoryboardPlanError, match="cover"):
        sp.validate_plan(_plan(ctx), clip_start_abs=9.0, clip_end_abs=20.0, known_file_names=ctx.by_name)


def test_plan_with_unknown_clip_is_rejected():
    ctx = _ctx(20)
    plan = _plan(ctx)
    plan["clips"][0]["file_name"] = "gone.mp4"
    with pytest.raises(sp.StoryboardPlanError, match="inventory"):
        sp.validate_plan(plan, clip_start_abs=10.0, clip_end_abs=20.0, known_file_names=ctx.by_name)


def test_plan_whose_clips_do_not_match_its_cuts_is_rejected():
    ctx = _ctx(20)
    plan = _plan(ctx)
    plan["switch_points_abs"] = [12.0, 16.0, 17.5]
    with pytest.raises(sp.StoryboardPlanError, match="spans"):
        sp.validate_plan(plan, clip_start_abs=10.0, clip_end_abs=20.0, known_file_names=ctx.by_name)
