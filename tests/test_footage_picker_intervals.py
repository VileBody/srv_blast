from __future__ import annotations

import pytest

from mlcore.footage_picker import (
    _deterministic_choose,
    build_intervals_from_switch_points,
    pick_footage_clips_by_intervals_deterministic,
)
from mlcore.models.footage_style import FootageStylePickPayload, FootageStyleRawPayload, FootageStyleRotation


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


def test_interval_picker_respects_exclude_file_names_when_possible() -> None:
    style = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    payload, diag = pick_footage_clips_by_intervals_deterministic(
        style_pick=style,
        assets=_assets(),
        clip_start_abs=0.0,
        clip_end_abs=4.0,
        switch_points_abs=[1.0, 2.5],  # 3 intervals
        seed_key="job-int-exclude-ok",
        exclude_file_names=["a.mp4"],
    )
    names = [str(c.file_name) for c in sorted(payload.clips, key=lambda c: float(c.in_point))]
    assert len(names) == 3
    assert "a.mp4" not in names
    assert diag.exclude_relaxed is False
    assert diag.selected_excluded_count == 0


def test_interval_picker_relaxes_exclude_file_names_when_pool_insufficient() -> None:
    style = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    payload, diag = pick_footage_clips_by_intervals_deterministic(
        style_pick=style,
        assets=_assets(),
        clip_start_abs=0.0,
        clip_end_abs=6.0,
        switch_points_abs=[1.0, 2.5, 4.0],  # 4 intervals
        seed_key="job-int-exclude-relax",
        exclude_file_names=["a.mp4", "b.mp4", "c.mp4"],
    )
    names = [str(c.file_name) for c in sorted(payload.clips, key=lambda c: float(c.in_point))]
    assert len(names) == 4
    assert diag.exclude_relaxed is True
    assert diag.selected_excluded_count >= 1


def test_interval_picker_raw_filters_selects_global_candidates_across_tags() -> None:
    style = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    raw = FootageStyleRawPayload.model_validate(
        {
            "theme": "betrayal_minor",
            "mood": "minor",
            "filters": {
                "color_priority": ["dark", "cold"],
                "exclude": ["couple", "crowd"],
                "priority_theme_tags": ["night city", "neon lights"],
            },
        }
    )
    mapped_assets = [
        {
            "file_name": "r1.mp4",
            "genre": "Rock",
            "tag": "dark_forest",
            "duration_sec": 2.0,
            "src_w": 720,
            "src_h": 1280,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "guys",
            "meta_theme_tags": ["night city"],
        },
        {
            "file_name": "r2.mp4",
            "genre": "Pop",
            "tag": "dream_aesthetic",
            "duration_sec": 2.0,
            "src_w": 720,
            "src_h": 1280,
            "meta_mood": "minor",
            "meta_color_tone": "cold",
            "meta_people_type": "none",
            "meta_theme_tags": ["neon lights"],
        },
        {
            "file_name": "r3.mp4",
            "genre": "Hip-Hop",
            "tag": "neon_city_night",
            "duration_sec": 2.0,
            "src_w": 720,
            "src_h": 1280,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "guys",
            "meta_theme_tags": ["night city", "streets"],
        },
    ]

    payload, diag = pick_footage_clips_by_intervals_deterministic(
        style_pick=style,
        assets=mapped_assets,
        clip_start_abs=0.0,
        clip_end_abs=3.0,
        switch_points_abs=[1.0, 2.0],
        seed_key="job-int-raw-global",
        raw_pick=raw,
    )
    clips = sorted(payload.clips, key=lambda c: float(c.in_point))
    assert len(clips) == 3
    names = [str(c.file_name) for c in clips]
    assert set(names).issubset({"r1.mp4", "r2.mp4", "r3.mp4"})
    assert diag.genre == "__raw_global__"
    assert diag.widened_to_genre is False
    assert diag.widened_to_global is False


def test_interval_picker_raw_filters_applies_strict_exclude_ban() -> None:
    style = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    raw = FootageStyleRawPayload.model_validate(
        {
            "theme": "heartbreak_minor",
            "mood": "minor",
            "filters": {
                "color_priority": ["dark"],
                "exclude": ["crowd"],
                "priority_theme_tags": ["night"],
            },
        }
    )
    mapped_assets = [
        {
            "file_name": "x_excluded.mp4",
            "genre": "Rock",
            "tag": "dark_forest",
            "duration_sec": 2.0,
            "src_w": 720,
            "src_h": 1280,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "crowd",
            "meta_theme_tags": ["night", "city"],
        },
        {
            "file_name": "x_ok.mp4",
            "genre": "Rock",
            "tag": "dark_forest",
            "duration_sec": 2.0,
            "src_w": 720,
            "src_h": 1280,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "none",
            "meta_theme_tags": ["night"],
        },
    ]

    payload, _diag = pick_footage_clips_by_intervals_deterministic(
        style_pick=style,
        assets=mapped_assets,
        clip_start_abs=0.0,
        clip_end_abs=1.0,
        switch_points_abs=[],
        seed_key="job-int-raw-penalty",
        raw_pick=raw,
    )
    clips = sorted(payload.clips, key=lambda c: float(c.in_point))
    assert len(clips) == 1
    assert str(clips[0].file_name) == "x_ok.mp4"


def test_interval_picker_raw_filters_exclude_bans_by_metadata_tag() -> None:
    style = FootageStylePickPayload.model_validate({"genre": "Rock", "tag": "dark_forest"})
    raw = FootageStyleRawPayload.model_validate(
        {
            "theme": "heartbreak_minor",
            "mood": "minor",
            "filters": {
                "color_priority": ["dark"],
                "exclude_tags": ["crowd"],
                "priority_theme_tags": ["night"],
            },
        }
    )
    mapped_assets = [
        {
            "file_name": "z_excluded_by_tag.mp4",
            "genre": "Rock",
            "tag": "dark_forest",
            "duration_sec": 2.0,
            "src_w": 720,
            "src_h": 1280,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "none",
            "meta_theme_tags": ["night", "crowd"],
        },
        {
            "file_name": "z_ok.mp4",
            "genre": "Rock",
            "tag": "dark_forest",
            "duration_sec": 2.0,
            "src_w": 720,
            "src_h": 1280,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "none",
            "meta_theme_tags": ["night"],
        },
    ]

    payload, _diag = pick_footage_clips_by_intervals_deterministic(
        style_pick=style,
        assets=mapped_assets,
        clip_start_abs=0.0,
        clip_end_abs=1.0,
        switch_points_abs=[],
        seed_key="job-int-raw-exclude-tag",
        raw_pick=raw,
    )
    clips = sorted(payload.clips, key=lambda c: float(c.in_point))
    assert len(clips) == 1
    assert str(clips[0].file_name) == "z_ok.mp4"


def test_style_rotation_allows_multiple_themes_with_same_mood() -> None:
    payload = FootageStyleRotation.model_validate(
        {
            "subgroups": [
                {
                    "artist_id": "rock_emo",
                    "theme": "heartbreak_minor",
                    "mood": "minor",
                    "tags_group": "g1",
                    "filters": {
                        "color_priority": ["dark"],
                        "exclude_people": ["crowd"],
                        "exclude_tags": [],
                        "priority_theme_tags": ["night city"],
                    },
                },
                {
                    "artist_id": "rock_emo",
                    "theme": "self_destruction_minor",
                    "mood": "minor",
                    "tags_group": "g2",
                    "filters": {
                        "color_priority": ["cold"],
                        "exclude_people": ["crowd"],
                        "exclude_tags": [],
                        "priority_theme_tags": ["blurry"],
                    },
                },
            ]
        }
    )
    assert len(payload.subgroups) == 2
    assert payload.subgroups[0].theme != payload.subgroups[1].theme


def test_raw_rotation_uses_theme_tags_overlap_not_inventory_genre_tag() -> None:
    """In v2 rotation, the picker selects on (artist_id-scoped assets) ∩
    priority_theme_tags ∩ exclusion filters. The legacy inventory genre/tag
    filter is intentionally NOT applied — that responsibility moved to the
    orchestrator, which now pre-scopes `assets` by artist_id before invoking
    the picker. Regression for a production crash where genre=tag="ХипХоп"
    nuked the entire pool because no asset had that exact (genre, tag) pair.
    """
    style = FootageStylePickPayload.model_validate({"genre": "Alternative", "tag": "art_rock"})
    raw_picks = [
        FootageStyleRawPayload.model_validate(
            {
                "theme": "alt_theme",
                "mood": "minor",
                "tags_group": "g1",
                "filters": {
                    "color_priority": ["dark"],
                    "exclude": [],
                    "priority_theme_tags": ["night city"],
                },
            }
        ),
    ]
    # Assets here are presumed already scoped to one artist; their `genre`
    # and `tag` fields are ignored by the picker. What matters is the
    # priority_theme_tags overlap.
    mapped_assets = [
        {
            "file_name": "asset_1.mp4",
            "genre": "ХипХоп",
            "tag": "ХипХоп",  # genre==tag — no specific inventory subgroup
            "duration_sec": 2.5,
            "src_w": 720,
            "src_h": 1280,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "none",
            "meta_theme_tags": ["night city"],
        },
        {
            "file_name": "asset_2.mp4",
            "genre": "ХипХоп",
            "tag": "ХипХоп",
            "duration_sec": 2.5,
            "src_w": 720,
            "src_h": 1280,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "none",
            "meta_theme_tags": ["night city", "neon"],
        },
    ]

    payload, _diag = pick_footage_clips_by_intervals_deterministic(
        style_pick=style,
        assets=mapped_assets,
        clip_start_abs=0.0,
        clip_end_abs=3.0,
        switch_points_abs=[1.0, 2.0],
        seed_key="job-int-raw-rotation-style-guard",
        raw_picks=raw_picks,
    )
    names = [str(c.file_name) for c in sorted(payload.clips, key=lambda c: float(c.in_point))]
    assert len(names) == 3
    # Both pre-scoped assets are valid; picker uses theme overlap only,
    # not inventory genre/tag (which mismatches Alternative/art_rock here).
    assert set(names).issubset({"asset_1.mp4", "asset_2.mp4"})


def test_raw_priority_selection_moves_to_next_subgroup_when_first_is_exhausted() -> None:
    style = FootageStylePickPayload.model_validate({"genre": "Alternative", "tag": "art_rock"})
    raw_picks = [
        FootageStyleRawPayload.model_validate(
            {
                "theme": "alt_theme",
                "mood": "minor",
                "tags_group": "priority_first",
                "filters": {
                    "color_priority": ["dark"],
                    "exclude": [],
                    "priority_theme_tags": ["first-group-tag"],
                },
            }
        ),
        FootageStyleRawPayload.model_validate(
            {
                "theme": "alt_theme",
                "mood": "minor",
                "tags_group": "secondary",
                "filters": {
                    "color_priority": ["dark"],
                    "exclude": [],
                    "priority_theme_tags": ["second-group-tag"],
                },
            }
        ),
    ]
    mapped_assets = [
        {
            "file_name": "first_pool.mp4",
            "genre": "Alternative",
            "tag": "art_rock",
            "duration_sec": 2.5,
            "src_w": 720,
            "src_h": 1280,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "none",
            "meta_theme_tags": ["first-group-tag"],
        },
        {
            "file_name": "second_pool.mp4",
            "genre": "Alternative",
            "tag": "art_rock",
            "duration_sec": 2.5,
            "src_w": 720,
            "src_h": 1280,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "none",
            "meta_theme_tags": ["second-group-tag"],
        },
    ]

    payload, diag = pick_footage_clips_by_intervals_deterministic(
        style_pick=style,
        assets=mapped_assets,
        clip_start_abs=0.0,
        clip_end_abs=1.5,
        switch_points_abs=[0.75],
        seed_key="job-int-raw-priority-v2",
        raw_picks=raw_picks,
    )
    names = [str(c.file_name) for c in sorted(payload.clips, key=lambda c: float(c.in_point))]
    assert names == ["first_pool.mp4", "second_pool.mp4"]
    assert diag.selection_mode == "raw_priority_v2"
    assert len(diag.interval_trace) == 2
    assert int(diag.interval_trace[0]["selected_subgroup_idx"]) == 0
    assert int(diag.interval_trace[1]["selected_subgroup_idx"]) == 1


def test_raw_priority_selection_falls_forward_to_next_theme_when_first_theme_empty() -> None:
    style = FootageStylePickPayload.model_validate({"genre": "Alternative", "tag": "art_rock"})
    raw_picks = [
        FootageStyleRawPayload.model_validate(
            {
                "theme": "theme_a",
                "mood": "minor",
                "tags_group": "empty_group",
                "filters": {
                    "color_priority": ["dark"],
                    "exclude": [],
                    "priority_theme_tags": ["tag-missing-in-inventory"],
                },
            }
        ),
        FootageStyleRawPayload.model_validate(
            {
                "theme": "theme_b",
                "mood": "minor",
                "tags_group": "working_group",
                "filters": {
                    "color_priority": ["dark"],
                    "exclude": [],
                    "priority_theme_tags": ["tag-present"],
                },
            }
        ),
    ]
    mapped_assets = [
        {
            "file_name": "working_1.mp4",
            "genre": "Alternative",
            "tag": "art_rock",
            "duration_sec": 2.5,
            "src_w": 720,
            "src_h": 1280,
            "meta_mood": "minor",
            "meta_color_tone": "dark",
            "meta_people_type": "none",
            "meta_theme_tags": ["tag-present"],
        },
    ]

    payload, diag = pick_footage_clips_by_intervals_deterministic(
        style_pick=style,
        assets=mapped_assets,
        clip_start_abs=0.0,
        clip_end_abs=1.0,
        switch_points_abs=[],
        seed_key="job-int-raw-priority-theme-fall-forward",
        raw_picks=raw_picks,
    )
    names = [str(c.file_name) for c in sorted(payload.clips, key=lambda c: float(c.in_point))]
    assert names == ["working_1.mp4"]
    assert diag.selection_mode == "raw_priority_v2"
    assert len(diag.interval_trace) == 1
    attempts = list(diag.interval_trace[0].get("attempts") or [])
    assert len(attempts) >= 2
    assert int(attempts[0]["subgroup_idx"]) == 0
    assert int(attempts[0]["candidate_count"]) == 0
    assert int(attempts[1]["subgroup_idx"]) == 1
    assert int(attempts[1]["candidate_count"]) >= 1
