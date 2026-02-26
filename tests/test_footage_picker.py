from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from mlcore.footage_picker import (
    build_style_groups_from_assets,
    deterministic_seed_from_key,
    pick_footage_clips_deterministic,
    validate_style_pick_in_groups,
)
from mlcore.models.footage_style import FootageStylePickPayload


def _assets() -> list[dict]:
    return [
        {"file_name": "a.mp4", "genre": "Rock", "tag": "dark_forest", "duration_sec": 3.0, "src_w": 720, "src_h": 1280},
        {"file_name": "b.mp4", "genre": "Rock", "tag": "dark_forest", "duration_sec": 4.0, "src_w": 720, "src_h": 1280},
        {"file_name": "c.mp4", "genre": "Rock", "tag": "rain_aesthetic", "duration_sec": 5.0, "src_w": 720, "src_h": 1280},
        {"file_name": "d.mp4", "genre": "Pop", "tag": "dream_aesthetic", "duration_sec": 6.0, "src_w": 720, "src_h": 1280},
    ]


def _assert_no_gaps(payload, *, clip_start: float, clip_end: float) -> None:
    clips = sorted(payload.clips, key=lambda c: float(c.in_point))
    assert abs(float(clips[0].in_point) - float(clip_start)) <= 1e-6
    assert abs(float(clips[-1].out_point) - float(clip_end)) <= 1e-6
    for i in range(len(clips) - 1):
        assert abs(float(clips[i].out_point) - float(clips[i + 1].in_point)) <= 1e-6


def test_style_pick_validation_requires_genre_and_tag_and_pool_membership() -> None:
    with pytest.raises(ValidationError):
        FootageStylePickPayload.model_validate({"genre": "Rock"})

    groups = build_style_groups_from_assets(_assets())
    ok = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    validate_style_pick_in_groups(ok, groups)

    missing = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "missing"})
    with pytest.raises(RuntimeError, match="not present in style pool"):
        validate_style_pick_in_groups(missing, groups)


def test_deterministic_shuffle_reproducibility() -> None:
    assets = _assets()
    style = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    p1, d1 = pick_footage_clips_deterministic(
        style_pick=style,
        assets=assets,
        clip_start_abs=0.0,
        clip_end_abs=11.0,
        seed_key="job-42",
    )
    p2, d2 = pick_footage_clips_deterministic(
        style_pick=style,
        assets=assets,
        clip_start_abs=0.0,
        clip_end_abs=11.0,
        seed_key="job-42",
    )

    names1 = [c.file_name for c in p1.clips]
    names2 = [c.file_name for c in p2.clips]
    assert names1 == names2
    assert d1.deterministic_seed == d2.deterministic_seed
    assert d1.deterministic_seed == deterministic_seed_from_key("job-42")


def test_picker_covers_window_without_gaps_and_with_asset_duration_caps() -> None:
    assets = _assets()
    by_name = {a["file_name"]: float(a["duration_sec"]) for a in assets}
    style = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    payload, _diag = pick_footage_clips_deterministic(
        style_pick=style,
        assets=assets,
        clip_start_abs=10.0,
        clip_end_abs=19.0,
        seed_key="job-cover",
    )

    _assert_no_gaps(payload, clip_start=10.0, clip_end=19.0)
    for c in payload.clips:
        clip_len = float(c.out_point) - float(c.in_point)
        assert clip_len <= by_name[c.file_name] + 1e-6
        assert abs(float(c.start_time) - float(c.in_point)) <= 1e-6


def test_insufficient_primary_widens_to_same_genre() -> None:
    assets = [
        {"file_name": "p1.mp4", "genre": "Rock", "tag": "dark_forest", "duration_sec": 2.0, "src_w": 720, "src_h": 1280},
        {"file_name": "g1.mp4", "genre": "Rock", "tag": "rain_aesthetic", "duration_sec": 4.0, "src_w": 720, "src_h": 1280},
        {"file_name": "g2.mp4", "genre": "Rock", "tag": "vintage_concert", "duration_sec": 4.0, "src_w": 720, "src_h": 1280},
    ]
    style = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    payload, diag = pick_footage_clips_deterministic(
        style_pick=style,
        assets=assets,
        clip_start_abs=0.0,
        clip_end_abs=6.0,
        seed_key="job-widen",
    )
    _assert_no_gaps(payload, clip_start=0.0, clip_end=6.0)
    assert diag.widened_to_genre is True
    assert diag.repeats_used is False
    assert any(c.file_name in {"g1.mp4", "g2.mp4"} for c in payload.clips)


def test_insufficient_genre_total_enables_repeats() -> None:
    assets = [
        {"file_name": "p1.mp4", "genre": "Rock", "tag": "dark_forest", "duration_sec": 2.0, "src_w": 720, "src_h": 1280},
        {"file_name": "g1.mp4", "genre": "Rock", "tag": "rain_aesthetic", "duration_sec": 2.0, "src_w": 720, "src_h": 1280},
    ]
    style = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    payload, diag = pick_footage_clips_deterministic(
        style_pick=style,
        assets=assets,
        clip_start_abs=0.0,
        clip_end_abs=9.0,
        seed_key="job-repeat",
    )
    _assert_no_gaps(payload, clip_start=0.0, clip_end=9.0)
    assert diag.widened_to_genre is True
    assert diag.repeats_used is True

    counts = Counter(c.file_name for c in payload.clips)
    assert any(v > 1 for v in counts.values())
