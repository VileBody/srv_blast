from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "build_static_assets_index",
    Path(__file__).resolve().parents[1] / "scripts" / "build_static_assets_index.py",
)
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)  # type: ignore

parse_key = _mod.parse_key
parse_ffprobe_json = _mod.parse_ffprobe_json


def test_parse_key_one_folder_level_genre_equals_tag() -> None:
    # pinterest_collection/pins2.../Alone_Girls/100275529199764783.mp4
    out = parse_key(
        "pinterest_collection/pins2_1to1_20260323/Alone_Girls/100275529199764783.mp4",
        "pinterest_collection/pins2_1to1_20260323",
    )
    assert out == ("100275529199764783.mp4", "Alone_Girls", "Alone_Girls")


def test_parse_key_two_folder_levels() -> None:
    out = parse_key(
        "pinterest_collection/Rock/dark_forest/1001276929637034910_.mp4",
        "pinterest_collection",
    )
    assert out == ("1001276929637034910_.mp4", "Rock", "dark_forest")


def test_parse_key_no_folder_returns_none() -> None:
    assert parse_key("pinterest_collection/justafile.mp4", "pinterest_collection") is None


def test_parse_key_tolerates_leading_slash_and_trailing_prefix_slash() -> None:
    out = parse_key("/pref/Genre/Tag/x.mp4", "pref/")
    assert out == ("x.mp4", "Genre", "Tag")


def test_parse_ffprobe_json_ok() -> None:
    raw = '{"streams":[{"width":720,"height":1280}],"format":{"duration":"5.253"}}'
    assert parse_ffprobe_json(raw) == (720, 1280, 5.253)


def test_parse_ffprobe_json_skips_audio_stream_to_find_video_dims() -> None:
    raw = '{"streams":[{"width":0,"height":0},{"width":1080,"height":1920}],"format":{"duration":"3.0"}}'
    assert parse_ffprobe_json(raw) == (1080, 1920, 3.0)


def test_parse_ffprobe_json_rejects_missing_dims_or_duration() -> None:
    assert parse_ffprobe_json('{"streams":[{"width":720,"height":1280}],"format":{}}') is None
    assert parse_ffprobe_json('{"streams":[],"format":{"duration":"5"}}') is None
    assert parse_ffprobe_json("not json") is None

def test_probe_s3_video_downloads_before_ffprobe(monkeypatch) -> None:
    downloaded = []

    def fake_download(bucket, key, dest):
        downloaded.append((bucket, key, dest.suffix))
        dest.write_bytes(b"video")
        return dest

    monkeypatch.setattr("src.storage.s3.download_from_s3", fake_download)
    monkeypatch.setattr(
        _mod,
        "_ffprobe_url",
        lambda path, **kwargs: '{"streams":[{"width":1080,"height":1920}],"format":{"duration":"4.25"}}',
    )

    assert _mod.probe_s3_video("assets", "root/a.mp4") == (1080, 1920, 4.25)
    assert downloaded == [("assets", "root/a.mp4", ".mp4")]