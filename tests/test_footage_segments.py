"""Virtual segmentation of long sources — the boundary maths and row expansion."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from mlcore.footage_segments import (
    MIN_SEGMENT_SEC,
    expand_asset_rows,
    make_segment_name,
    media_file_name,
    parse_segment_name,
    segment_bounds,
)


# --------------------------------------------------------------------------- #
# naming
# --------------------------------------------------------------------------- #
def test_segment_name_keeps_the_extension() -> None:
    # AE imports and the media fetcher both branch on the extension.
    assert make_segment_name("clip.mp4", 3) == "clip~seg03.mp4"
    assert parse_segment_name("clip~seg03.mp4") == ("clip.mp4", 3)


def test_plain_names_are_not_segments() -> None:
    assert parse_segment_name("clip.mp4") is None


# --------------------------------------------------------------------------- #
# boundaries
# --------------------------------------------------------------------------- #
def test_long_source_splits_into_even_windows() -> None:
    got = segment_bounds(100.0, target_sec=20.0)
    assert got == [(0.0, 20.0), (20.0, 40.0), (40.0, 60.0), (60.0, 80.0), (80.0, 100.0)]


def test_windows_are_contiguous_and_cover_the_source() -> None:
    got = segment_bounds(97.0, target_sec=20.0)
    assert got[0][0] == 0.0 and got[-1][1] == 97.0
    assert all(a[1] == b[0] for a, b in zip(got, got[1:]))


def test_short_remainder_is_absorbed_not_emitted() -> None:
    # A 3s tail could not cover any interval; emitting it would only add a row
    # that every _fits_interval check rejects.
    got = segment_bounds(83.0, target_sec=20.0)
    assert all(end - start >= MIN_SEGMENT_SEC for start, end in got)
    assert got[-1] == (60.0, 83.0)


def test_boundaries_snap_to_scene_cuts() -> None:
    # Cuts sit ~2s off the blind grid; each interior boundary should move onto one.
    got = segment_bounds(100.0, target_sec=20.0, scene_cuts=[18.5, 41.0, 62.0, 78.0])
    assert [round(b[0], 1) for b in got] == [0.0, 18.5, 41.0, 62.0, 78.0]


def test_far_away_cuts_do_not_drag_boundaries() -> None:
    got = segment_bounds(100.0, target_sec=20.0, scene_cuts=[55.0])
    assert got[0] == (0.0, 20.0)


def test_snapping_never_produces_an_undersized_window() -> None:
    # Dense cuts right after a boundary must not collapse the next window.
    cuts = [20.5, 21.0, 21.5, 22.0]
    got = segment_bounds(100.0, target_sec=20.0, scene_cuts=cuts)
    assert all(end - start >= MIN_SEGMENT_SEC for start, end in got)


def test_source_too_short_to_split_yields_one_window_or_none() -> None:
    assert segment_bounds(2.0, target_sec=20.0) == []
    assert segment_bounds(10.0, target_sec=20.0) == [(0.0, 10.0)]


# --------------------------------------------------------------------------- #
# row expansion
# --------------------------------------------------------------------------- #
def _row(name: str, duration: float) -> Dict[str, Any]:
    return {
        "file_name": name,
        "genre": "films",
        "tag": "interstellar",
        "duration_sec": duration,
        "src_w": 1920,
        "src_h": 1080,
    }


def _names(rows: List[Dict[str, Any]]) -> List[str]:
    return [str(r["file_name"]) for r in rows]


def test_long_row_becomes_many_rows_sharing_one_media_file() -> None:
    out = expand_asset_rows([_row("movie.mp4", 100.0)], target_sec=20.0, min_source=60.0)
    assert _names(out) == [f"movie~seg{i:02d}.mp4" for i in range(5)]
    # One physical object behind all of them — this is what makes the node
    # download it once instead of five times.
    assert {media_file_name(r) for r in out} == {"movie.mp4"}
    assert [r["segment_base_sec"] for r in out] == [0.0, 20.0, 40.0, 60.0, 80.0]


def test_short_row_is_passed_through_untouched() -> None:
    row = _row("short.mp4", 12.0)
    (out,) = expand_asset_rows([row], target_sec=20.0, min_source=60.0)
    assert out == row


def test_expansion_is_idempotent() -> None:
    once = expand_asset_rows([_row("movie.mp4", 100.0)], target_sec=20.0, min_source=60.0)
    twice = expand_asset_rows(once, target_sec=20.0, min_source=60.0)
    assert twice == once


def test_expansion_preserves_folder_and_geometry() -> None:
    out = expand_asset_rows([_row("movie.mp4", 100.0)], target_sec=20.0, min_source=60.0)
    assert all((r["genre"], r["tag"]) == ("films", "interstellar") for r in out)
    assert all((r["src_w"], r["src_h"]) == (1920, 1080) for r in out)


def test_scene_cuts_are_applied_per_file() -> None:
    rows = [_row("a.mp4", 100.0), _row("b.mp4", 100.0)]
    out = expand_asset_rows(
        rows, target_sec=20.0, min_source=60.0, scene_cuts_by_file={"a.mp4": [18.5]}
    )
    a = [r for r in out if media_file_name(r) == "a.mp4"]
    b = [r for r in out if media_file_name(r) == "b.mp4"]
    assert a[0]["duration_sec"] == 18.5
    assert b[0]["duration_sec"] == 20.0


@pytest.mark.parametrize("env_value, expected_first", [("10", 10.0), ("30", 30.0)])
def test_segment_length_is_env_tunable(
    monkeypatch: pytest.MonkeyPatch, env_value: str, expected_first: float
) -> None:
    monkeypatch.setenv("FOOTAGE_SEGMENT_SEC", env_value)
    monkeypatch.setenv("FOOTAGE_SEGMENT_MIN_SOURCE_SEC", "60")
    out = expand_asset_rows([_row("movie.mp4", 120.0)])
    assert out[0]["duration_sec"] == expected_first
