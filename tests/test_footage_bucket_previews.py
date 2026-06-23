"""Tests for the pure/core pieces of footage bucket preview generation
(precision flow, phase 4): clip-selection scoring/determinism, montage inputs,
description, and the footage_bucket_previews.json store. No S3/AE/Telegram I/O.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mlcore.footage_bucket_catalog import Bucket
from mlcore import footage_bucket_previews as bp


def _bucket(bucket_id="romance_major:nature_sunset", tags=None, color=None, mood="major",
            label="Природа / закат") -> Bucket:
    return Bucket(
        bucket_id=bucket_id,
        theme=bucket_id.split(":", 1)[0],
        tags_group=bucket_id.split(":", 1)[1],
        mood=mood,
        priority_tags=tags if tags is not None else ["sunset", "beach", "ocean"],
        exclude_tags=[],
        color=color if color is not None else ["warm", "light"],
        theme_label="Романтика",
        subtheme_label=label,
    )


def _asset(file_name, tags, *, color="warm", people="none"):
    return {
        "file_name": file_name,
        "genre": "g",
        "tag": "t",
        "duration_sec": 5.0,
        "src_w": 720,
        "src_h": 1280,
        "meta_theme_tags": list(tags),
        "meta_color_tone": color,
        "meta_people_type": people,
    }


# --------------------------------------------------------------------------- #
# clip selection
# --------------------------------------------------------------------------- #
def test_select_ranks_by_overlap_and_respects_top_n():
    b = _bucket(tags=["sunset", "beach", "ocean"])
    assets = [
        _asset("3000.mp4", ["sunset", "beach", "ocean"]),   # overlap 3
        _asset("3001.mp4", ["sunset", "beach"]),            # overlap 2
        _asset("3002.mp4", ["sunset"]),                     # overlap 1
        _asset("3003.mp4", ["city", "night"]),              # overlap 0 -> excluded
    ]
    clips = bp.select_bucket_clips(b, assets, seed="s", top_n=2)
    names = [c["file_name"] for c in clips]
    assert names == ["3000.mp4", "3001.mp4"]
    # zero-overlap clip is never selected
    all_clips = bp.select_bucket_clips(b, assets, seed="s", top_n=10)
    assert "3003.mp4" not in [c["file_name"] for c in all_clips]
    assert len(all_clips) == 3


def test_color_bonus_breaks_overlap_tie():
    b = _bucket(tags=["sunset", "beach"], color=["warm"])
    assets = [
        _asset("a.mp4", ["sunset", "beach"], color="cold"),   # overlap 2, no bonus
        _asset("b.mp4", ["sunset", "beach"], color="warm"),   # overlap 2 + 0.5
    ]
    clips = bp.select_bucket_clips(b, assets, seed="s", top_n=1)
    assert clips[0]["file_name"] == "b.mp4"


def test_selection_is_deterministic_for_same_seed():
    b = _bucket(tags=["sunset", "beach", "ocean"])
    assets = [_asset(f"{i}.mp4", ["sunset", "beach"]) for i in range(10)]
    one = [c["file_name"] for c in bp.select_bucket_clips(b, assets, seed="abc", top_n=5)]
    two = [c["file_name"] for c in bp.select_bucket_clips(b, assets, seed="abc", top_n=5)]
    other = [c["file_name"] for c in bp.select_bucket_clips(b, assets, seed="xyz", top_n=5)]
    assert one == two
    # different seed should (very likely) reorder the equal-score ties
    assert one != other


def test_clip_ids_extracted_from_file_name():
    clips = [{"file_name": "100275529199764783.mp4"}, {"file_name": "no_id.mp4"}]
    assert bp.clip_ids_of(clips) == ["100275529199764783"]


# --------------------------------------------------------------------------- #
# description
# --------------------------------------------------------------------------- #
def test_description_includes_label_tags_and_mood():
    d = bp.build_bucket_description(_bucket(mood="minor"))
    assert "Природа / закат" in d
    assert "sunset" in d
    assert "минор" in d


# --------------------------------------------------------------------------- #
# montage inputs
# --------------------------------------------------------------------------- #
def test_montage_spec_and_jsx_injection():
    b = _bucket()
    clips = [_asset("a.mp4", ["sunset"]), _asset("b.mp4", ["beach"])]
    spec = bp.build_montage_spec(b, clips, comp_name="Bucket Preview")
    assert spec["width"] == 1080 and spec["height"] == 1920
    assert spec["label"] == b.label
    assert [c["relpath"] for c in spec["clips"]] == ["media/video/a.mp4", "media/video/b.mp4"]

    template = "head\n/*__MONTAGE_DATA__*/\ntail"
    out = bp.render_montage_jsx(spec, template)
    assert "var MONTAGE = " in out and "/*__MONTAGE_DATA__*/" not in out
    # the injected blob is valid JSON
    blob = out.split("var MONTAGE = ", 1)[1].rsplit(";", 1)[0]
    assert json.loads(blob)["comp_name"] == "Bucket Preview"


def test_render_montage_jsx_requires_marker():
    with pytest.raises(RuntimeError):
        bp.render_montage_jsx({}, "no marker here")


def test_media_payload_maps_urls_and_raises_on_missing():
    clips = [_asset("a.mp4", ["x"]), _asset("b.mp4", ["y"])]
    media = bp.montage_media_payload(
        clips, url_by_file_name={"a.mp4": "s3://bkt/a.mp4", "b.mp4": "s3://bkt/b.mp4"}
    )
    assert media == [
        {"url": "s3://bkt/a.mp4", "relpath": "media/video/a.mp4"},
        {"url": "s3://bkt/b.mp4", "relpath": "media/video/b.mp4"},
    ]
    with pytest.raises(RuntimeError):
        bp.montage_media_payload(clips, url_by_file_name={"a.mp4": "s3://bkt/a.mp4"})


# --------------------------------------------------------------------------- #
# previews store
# --------------------------------------------------------------------------- #
def test_store_upsert_has_preview_and_save_roundtrip(tmp_path: Path):
    store = bp.empty_store()
    assert store == {"version": bp.PREVIEWS_STORE_VERSION, "previews": {}}

    e = bp.PreviewEntry(
        bucket_id="romance_major:nature_sunset",
        label="Природа / закат",
        description="desc",
        s3_url="s3://bkt/romance_major__nature_sunset.mp4",
        file_id="FID",
        clip_ids=["100275529199764783"],
        built_at=bp.now_iso(),
    )
    bp.previews_upsert(store, e)
    assert bp.has_preview(store, "romance_major:nature_sunset")
    assert not bp.has_preview(store, "unknown:bucket")

    p = tmp_path / "footage_bucket_previews.json"
    bp.save_previews_store(p, store)
    loaded = bp.load_previews_store(p)
    assert loaded["previews"]["romance_major:nature_sunset"]["file_id"] == "FID"
    # round-trips structurally
    assert bp.has_preview(loaded, "romance_major:nature_sunset")


def test_thin_entry_is_not_a_usable_preview():
    store = bp.empty_store()
    thin = bp.PreviewEntry(bucket_id="x:y", status="thin", s3_url="", file_id="")
    bp.previews_upsert(store, thin)
    # thin / no media -> re-run should still attempt it
    assert not bp.has_preview(store, "x:y")
    assert bp.has_preview(store, "x:y", require_ok=False) is False  # no media at all


def test_load_missing_store_returns_empty(tmp_path: Path):
    assert bp.load_previews_store(tmp_path / "nope.json") == bp.empty_store()


# --------------------------------------------------------------------------- #
# telegram file_id capture (response parsing only; network mocked)
# --------------------------------------------------------------------------- #
import importlib.util  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "build_bucket_previews",
    Path(__file__).resolve().parents[1] / "scripts" / "build_bucket_previews.py",
)
_script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_script)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_capture_telegram_file_id_parses_video_file_id(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00\x01")
    captured = {}

    def fake_post(url, data=None, files=None, timeout=None):
        captured["url"] = url
        captured["chat_id"] = data["chat_id"]
        return _FakeResp({"ok": True, "result": {"video": {"file_id": "ABC123"}}})

    monkeypatch.setattr(_script.requests, "post", fake_post)
    fid = _script.capture_telegram_file_id(
        token="TKN", chat_id="42", video_path=video, caption="hi"
    )
    assert fid == "ABC123"
    assert captured["url"].endswith("/botTKN/sendVideo")
    assert captured["chat_id"] == "42"


def test_capture_telegram_file_id_raises_on_not_ok(tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")

    monkeypatch.setattr(
        _script.requests, "post",
        lambda *a, **k: _FakeResp({"ok": False, "description": "blocked"}),
    )
    with pytest.raises(RuntimeError):
        _script.capture_telegram_file_id(token="T", chat_id="1", video_path=video, caption="c")


def test_parse_s3_url():
    assert _script._parse_s3_url("s3://bkt/a/b.mp4") == ("bkt", "a/b.mp4")
    assert _script._parse_s3_url("https://x/y.mp4") is None
    assert _script._parse_s3_url("s3://nokey") is None
