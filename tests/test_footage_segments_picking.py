"""Virtual segments through the picker and the render layer.

The claim being tested: one long upload yields MANY cuts, each opening inside its
own window, and the render layer still fetches the file once.
"""
from __future__ import annotations

from typing import Any, Dict, List

from mlcore.footage_picker import (
    _deterministic_choose,
    _source_offset_for_asset,
    load_picker_assets_from_inventory,
)
from mlcore.footage_segments import expand_asset_rows, media_file_name


def _long_inventory(duration: float = 100.0) -> Dict[str, Any]:
    rows = expand_asset_rows(
        [
            {
                "file_name": "movie.mp4",
                "file_path": "s3://b/films/interstellar/movie.mp4",
                "genre": "films",
                "tag": "interstellar",
                "duration_sec": duration,
                "src_w": 1920,
                "src_h": 1080,
            }
        ],
        target_sec=20.0,
        min_source=60.0,
    )
    return {"assets": rows}


def test_one_long_file_becomes_many_pickable_clips() -> None:
    # Without segmentation the strict no-repeat policy caps this at ONE cut.
    assets = load_picker_assets_from_inventory(_long_inventory())
    assert len(assets) == 5
    assert len({str(a["file_name"]) for a in assets}) == 5


def test_picker_rows_carry_the_window_and_the_real_media_name() -> None:
    assets = load_picker_assets_from_inventory(_long_inventory())
    assert [a["segment_base_sec"] for a in assets] == [0.0, 20.0, 40.0, 60.0, 80.0]
    assert {media_file_name(a) for a in assets} == {"movie.mp4"}


def test_offset_stays_inside_its_own_window() -> None:
    assets = load_picker_assets_from_inventory(_long_inventory())
    for idx, asset in enumerate(assets):
        base = float(asset["segment_base_sec"])
        off = _source_offset_for_asset(
            asset=asset,
            file_name=str(asset["file_name"]),
            interval_len=4.0,
            seed_value=12345,
            interval_idx=idx,
            offset_enabled=True,
        )
        # Inside [base, base + window - interval]; never bleeding into a neighbour.
        assert base <= off <= base + float(asset["duration_sec"]) - 4.0


def test_disabling_the_random_offset_still_seeks_to_the_window() -> None:
    # Regression: treating "offset off" as 0.0 would make every segment of a long
    # source open on the file's first frames.
    assets = load_picker_assets_from_inventory(_long_inventory())
    offs = [
        _source_offset_for_asset(
            asset=a,
            file_name=str(a["file_name"]),
            interval_len=4.0,
            seed_value=1,
            interval_idx=i,
            offset_enabled=False,
        )
        for i, a in enumerate(assets)
    ]
    assert offs == [0.0, 20.0, 40.0, 60.0, 80.0]


def test_a_plain_asset_is_unaffected() -> None:
    plain = {
        "file_name": "clip.mp4",
        "genre": "pop",
        "tag": "sad",
        "duration_sec": 12.0,
        "src_w": 1080,
        "src_h": 1920,
    }
    (asset,) = load_picker_assets_from_inventory({"assets": [plain]})
    assert "segment_base_sec" not in asset
    assert _source_offset_for_asset(
        asset=asset,
        file_name="clip.mp4",
        interval_len=4.0,
        seed_value=1,
        interval_idx=0,
        offset_enabled=False,
    ) == 0.0


def test_adjacent_cuts_never_reuse_the_same_source_file() -> None:
    # Two segments of one film back to back read as a jump cut inside one shot.
    assets: List[Dict[str, Any]] = load_picker_assets_from_inventory(_long_inventory())
    other = {
        "file_name": "b.mp4",
        "genre": "films",
        "tag": "interstellar",
        "duration_sec": 12.0,
        "src_w": 1920,
        "src_h": 1080,
    }
    candidates = [*assets, other]
    chosen = _deterministic_choose(
        candidates=candidates,
        seed_value=7,
        interval_idx=1,
        interval_start=4.0,
        avoid_file_name="movie.mp4",
    )
    assert media_file_name(chosen) == "b.mp4"


def test_render_layer_fetches_one_object_for_all_segments() -> None:
    from app.footage_comp import _resolve_safe_media_name

    assets = load_picker_assets_from_inventory(_long_inventory())
    used: set = set()
    by_original: Dict[str, str] = {}
    safe = [
        _resolve_safe_media_name(
            original=media_file_name(a), used_names=used, by_original=by_original
        )
        for a in assets
    ]
    # One relpath => render_manifest dedupes to a single download and AE imports once.
    assert len(set(safe)) == 1
