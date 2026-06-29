"""Tests for the deterministic kick-driven cut-timing generator."""

from __future__ import annotations

from mlcore.switch_timing_deterministic import (
    SwitchTimingParams,
    generate_switch_points,
)


def _gaps(pts):
    return [round(pts[i] - pts[i - 1], 3) for i in range(1, len(pts))]


def test_straight_bass_is_throttled_no_mush():
    """Kick every 0.5s must NOT produce a cut every 0.5s — the section spacing
    is the minimum gap (anti-mush)."""
    onsets = [(round(0.5 * i, 3), "kick", 0.4) for i in range(1, 40)]
    beats = [round(0.5 * i, 3) for i in range(40)]
    r = generate_switch_points(
        onsets_classified=onsets, beats=beats, bpm=120.0,
        drop_t=None, clip_start=0.0, clip_end=20.0,
    )
    gaps = _gaps(r.switch_points_abs)
    assert gaps, "expected cuts"
    # default gap at 120bpm = max(2 beats=1.0, floor 1.4) -> ~1.4-1.5 after beat snap
    assert min(gaps) >= 1.3
    assert len(r.switch_points_abs) < 20  # far fewer than the 39 kicks


def test_drop_window_is_denser_but_floored():
    """The 3s after the drop tightens spacing, but never below ~0.8-1.0s."""
    onsets = [(round(0.5 * i, 3), "kick", 0.4) for i in range(1, 40)]
    beats = [round(0.5 * i, 3) for i in range(40)]
    r = generate_switch_points(
        onsets_classified=onsets, beats=beats, bpm=120.0,
        drop_t=8.0, clip_start=0.0, clip_end=20.0,
    )
    drop_gaps = [
        g for c, g in zip(r.switch_points_abs[1:], _gaps(r.switch_points_abs))
        if 8.0 <= c <= 11.0
    ]
    assert drop_gaps, "expected cuts in the drop window"
    assert min(drop_gaps) >= 0.8          # never tighter than the floor
    assert min(drop_gaps) <= 1.05         # but it IS denser than default


# Logic tests use an explicit small-gap params object so they assert the
# priority/fallback rules independent of the production tuning constants.
_LOGIC = SwitchTimingParams(
    default_gap_beats=0.0, default_gap_floor_sec=0.5,
    drop_gap_beats=0.0, drop_gap_floor_sec=0.5,
    beat_snap_tol_sec=0.0, search_back_frac=0.4, search_fwd_frac=0.8,
)


def test_kick_beats_snare():
    """When a kick and a snare are both near the target, the kick wins."""
    onsets = [(0.6, "kick", 0.3), (0.65, "snare", 0.9)]
    r = generate_switch_points(
        onsets_classified=onsets, beats=[], bpm=120.0,
        drop_t=None, clip_start=0.0, clip_end=4.0, params=_LOGIC,
    )
    assert r.switch_points_abs == [0.6]
    assert r.sources[0] == "kick"


def test_snare_fallback_when_no_kick():
    onsets = [(0.6, "snare", 0.9), (1.2, "snare", 0.9)]
    r = generate_switch_points(
        onsets_classified=onsets, beats=[], bpm=120.0,
        drop_t=None, clip_start=0.0, clip_end=4.0, params=_LOGIC,
    )
    assert r.switch_points_abs and r.sources[0] == "snare"


def test_does_not_invent_cuts_when_lows_sparse():
    """A long stretch with no low onsets (and no beats to fall back on) yields a
    long hold, not a synthetic cut (user rule: 'не выдумывать')."""
    onsets = [(0.6, "kick", 0.4), (12.0, "kick", 0.4)]
    r = generate_switch_points(
        onsets_classified=onsets, beats=[], bpm=120.0,
        drop_t=None, clip_start=0.0, clip_end=14.0, params=_LOGIC,
    )
    # only the two real kicks become cuts — nothing invented in the 0.6..12 gap
    assert r.switch_points_abs == [0.6, 12.0]


def test_beat_fill_caps_long_holds():
    """With beats available, a stretch without lows is filled on-beat instead of
    holding past max_hold_sec (user rule #2)."""
    onsets = [(0.6, "kick", 0.4), (12.0, "kick", 0.4)]
    beats = [round(0.5 * i, 3) for i in range(28)]  # a beat every 0.5s
    p = SwitchTimingParams(
        default_gap_beats=0.0, default_gap_floor_sec=1.0,
        drop_gap_floor_sec=1.0, max_hold_sec=3.5, beat_snap_tol_sec=0.0,
    )
    r = generate_switch_points(
        onsets_classified=onsets, beats=beats, bpm=120.0,
        drop_t=None, clip_start=0.0, clip_end=14.0, params=p,
    )
    gaps = _gaps(r.switch_points_abs)
    assert max(gaps) <= 3.5 + 1e-6  # no static hold longer than max_hold
    assert "beat" in r.sources       # the gap was bridged on-beat


def test_hard_floor_respected():
    onsets = [(t, "kick", 0.4) for t in (1.4, 1.45, 1.5, 2.9, 3.0)]
    p = SwitchTimingParams(hard_floor_sec=0.3)
    r = generate_switch_points(
        onsets_classified=onsets, beats=[], bpm=120.0,
        drop_t=None, clip_start=0.0, clip_end=5.0, params=p,
    )
    gaps = _gaps(r.switch_points_abs)
    assert all(g >= 0.3 for g in gaps)


def test_empty_input_is_safe():
    r = generate_switch_points(
        onsets_classified=[], beats=[], bpm=0.0,
        drop_t=None, clip_start=0.0, clip_end=10.0,
    )
    assert r.switch_points_abs == []
