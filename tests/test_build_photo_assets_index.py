from __future__ import annotations

from scripts.build_photo_assets_index import parse_ffprobe_dims
from scripts.export_footage_tags_snapshot import _parse_args


def test_parse_ffprobe_dims_picks_first_valid_stream() -> None:
    raw = '{"streams":[{"width":0,"height":0},{"width":1920,"height":1440}]}'
    assert parse_ffprobe_dims(raw) == (1920, 1440)


def test_parse_ffprobe_dims_none_when_missing() -> None:
    assert parse_ffprobe_dims('{"streams":[]}') is None
    assert parse_ffprobe_dims("not json") is None


def test_export_args_default_video() -> None:
    out, source = _parse_args([])
    assert source == "video"
    assert out == "data/footage_tags_snapshot.json"


def test_export_args_photo_source_default_out() -> None:
    out, source = _parse_args(["--source", "photo"])
    assert source == "photo"
    assert out == "data/photo_tags_snapshot.json"


def test_export_args_explicit_out_with_source_eq() -> None:
    out, source = _parse_args(["custom/out.json", "--source=photo"])
    assert source == "photo"
    assert out == "custom/out.json"


def test_export_args_rejects_bad_source() -> None:
    import pytest

    with pytest.raises(SystemExit):
        _parse_args(["--source", "audio"])
