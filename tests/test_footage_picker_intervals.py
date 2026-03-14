from __future__ import annotations

import pytest

from mlcore.footage_picker import (
    _deterministic_choose,
    build_intervals_from_switch_points,
    pick_footage_clips_by_intervals_deterministic,
)
from mlcore.models.footage_style import FootageStylePickPayload


def _assets() -> list[dict]:
    return [
        {"file_name": "a.mp4", "genre": "Rock", "tag": "dark_forest", "duration_sec": 1.2, "src_w": 720, "src_h": 1280},
        {"file_name": "b.mp4", "genre": "Rock", "tag": "dark_forest", "duration_sec": 2.0, "src_w": 720, "src_h": 1280},
        {"file_name": "c.mp4", "genre": "Rock", "tag": "rain_aesthetic", "duration_sec": 3.2, "src_w": 720, "src_h": 1280},
        {"file_name": "d.mp4", "genre": "Pop", "tag": "dream_aesthetic", "duration_sec": 2.8, "src_w": 720, "src_h": 1280},
    ]


def test_build_intervals_from_switch_points() -> None:
    intervals = build_intervals_from_switch_points(
        clip_start_abs=0.0,
        clip_end_abs=6.0,
        switch_points_abs=[1.0, 2.5, 4.0],
    )
    assert intervals == [(0.0, 1.0), (1.0, 2.5), (2.5, 4.0), (4.0, 6.0)]


def test_interval_picker_assigns_one_clip_per_interval() -> None:
    style = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    payload, diag = pick_footage_clips_by_intervals_deterministic(
        style_pick=style,
        assets=_assets(),
        clip_start_abs=0.0,
        clip_end_abs=6.0,
        switch_points_abs=[1.0, 2.5, 4.0],
        seed_key="job-int-1",
    )
    clips = sorted(payload.clips, key=lambda c: float(c.in_point))
    assert len(clips) == 4
    assert abs(float(clips[0].in_point) - 0.0) <= 1e-6
    assert abs(float(clips[-1].out_point) - 6.0) <= 1e-6
    for i in range(len(clips) - 1):
        assert abs(float(clips[i].out_point) - float(clips[i + 1].in_point)) <= 1e-6
    assert diag.intervals_count == 4
    assert diag.widened_to_genre is True
    assert diag.widened_to_global is True
    assert diag.repeats_used is False
    names = [str(c.file_name) for c in clips]
    assert len(names) == len(set(names))


def test_interval_picker_fails_when_interval_too_long_for_pool() -> None:
    style = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    with pytest.raises(RuntimeError, match="No footage asset can cover interval"):
        pick_footage_clips_by_intervals_deterministic(
            style_pick=style,
            assets=_assets(),
            clip_start_abs=0.0,
            clip_end_abs=8.0,
            switch_points_abs=[1.0],
            seed_key="job-int-2",
        )


def test_deterministic_choose_avoids_immediate_repeat_when_possible() -> None:
    candidates = [
        {"file_name": "a.mp4"},
        {"file_name": "b.mp4"},
    ]
    chosen = _deterministic_choose(
        candidates=candidates,
        seed_value=12345,
        interval_idx=0,
        interval_start=0.0,
        avoid_file_name="a.mp4",
    )
    assert str(chosen["file_name"]) == "b.mp4"


def test_interval_picker_uses_repeats_when_unique_assets_not_enough() -> None:
    style = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    payload, diag = pick_footage_clips_by_intervals_deterministic(
        style_pick=style,
        assets=_assets(),
        clip_start_abs=0.0,
        clip_end_abs=6.5,
        switch_points_abs=[1.0, 2.0, 3.0, 4.0],  # 5 intervals, but only 4 unique files in whole inventory
        seed_key="job-int-unique-fail",
    )
    names = [str(c.file_name) for c in sorted(payload.clips, key=lambda c: float(c.in_point))]
    assert len(names) == 5
    assert len(set(names)) < len(names)
    assert diag.repeats_used is True
