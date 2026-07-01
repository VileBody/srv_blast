from __future__ import annotations

from mlcore.footage_tags_db import (
    SOURCE_PHOTO,
    SOURCE_VIDEO,
    build_photo_tag_record,
    build_tag_record,
    photo_clip_id,
)
from pathlib import Path

from mlcore.photo_tagger import (
    _downscale_photo,
    record_from_photo_result,
    run_photo_tagging_batch,
    select_untagged_photo_keys,
)


def test_photo_clip_id_namespaced_and_stable() -> None:
    a = photo_clip_id("photo_2026-06-29_19-18-16.jpg")
    assert a == "photo:photo_2026-06-29_19-18-16"
    # path/key forms resolve to the same id (basename, ext stripped, normalized)
    assert photo_clip_id("dump/warm/photo_2026-06-29_19-18-16.JPG") == a
    # no embedded 8-digit clip id required (unlike video)
    assert photo_clip_id("") is None


def test_photo_id_never_collides_with_video_clip_id() -> None:
    # Video ids are pure digits; photo ids are 'photo:'-prefixed -> disjoint PK space.
    vid = build_tag_record({"video_key": "Rock__x__12345678_a", "theme_tags": ["x"]})
    pho = build_photo_tag_record({"file_name": "12345678_a.jpg", "theme_tags": ["x"]})
    assert vid is not None and pho is not None
    assert vid["clip_id"] == "12345678"
    assert pho["clip_id"] == "photo:12345678_a"
    assert vid["clip_id"] != pho["clip_id"]


def test_build_tag_record_stamps_video_source() -> None:
    rec = build_tag_record({"video_key": "12345678_a", "theme_tags": ["x"]})
    assert rec is not None and rec["source"] == SOURCE_VIDEO


def test_build_photo_record_normalizes_and_keys() -> None:
    rec = build_photo_tag_record(
        {
            "file_name": "Sunset_Beach.JPG",
            "s3_key": "photo_collection/warm/Sunset_Beach.JPG",
            "mood": "MAJOR",
            "color_tone": "Warm",
            "people_type": "guy",  # -> guys
            "theme_tags": ["Sunset", "beach", "beach", " gold "],
        },
        tagger="groq",
    )
    assert rec is not None
    assert rec["source"] == SOURCE_PHOTO
    assert rec["clip_id"] == "photo:sunset_beach"
    assert rec["video_key"] == "Sunset_Beach.JPG"  # photo picker matches by file_name
    assert rec["mood"] == "major"
    assert rec["color_tone"] == "warm"
    assert rec["people_type"] == "guys"
    assert rec["theme_tags"] == ["sunset", "beach", "gold"]
    assert rec["tagger"] == "groq"


def test_build_photo_record_rejects_unkeyable_row() -> None:
    assert build_photo_tag_record({"file_name": "", "s3_key": ""}) is None


def test_record_from_photo_result_shape() -> None:
    rec = record_from_photo_result(
        s3_key="photo_collection/cold/rainy_street.png",
        result={"mood": "minor", "color_tone": "cold", "people_type": "none",
                "theme_tags": ["rain", "street"]},
    )
    assert rec is not None
    assert rec["source"] == SOURCE_PHOTO
    assert rec["clip_id"] == "photo:rainy_street"
    assert rec["theme_tags"] == ["rain", "street"]


def test_downscale_falls_back_to_src_when_ffmpeg_missing(tmp_path, monkeypatch) -> None:
    src = tmp_path / "big.jpg"
    src.write_bytes(b"not-a-real-jpeg")
    dst = tmp_path / "small.jpg"
    monkeypatch.setenv("FFMPEG_BIN", "definitely-not-a-real-ffmpeg-binary")
    out = _downscale_photo(src, dst)
    # resize failed -> original returned so tagging still proceeds
    assert out == src
    assert not dst.exists()


def test_batch_reports_providers_and_writes_with_injected_io(monkeypatch) -> None:
    # Qwen-lead chain must be visible in the summary; I/O fully injected.
    monkeypatch.setenv("DASHSCOPE_API_KEYS", "qwen-key-1")
    monkeypatch.setenv("GROQ_API_KEYS", "groq-key-1")

    written_records = []

    def list_keys_fn():
        return ["photo_collection/a/one.jpg", "photo_collection/a/two.png"]

    def fetch_tagged_fn():
        return set()

    def tag_fn(s3_key):
        return record_from_photo_result(
            s3_key=s3_key,
            result={"mood": "minor", "color_tone": "cold", "people_type": "none",
                    "theme_tags": ["rain"]},
        )

    def upsert_fn(records):
        written_records.extend(records)
        return len(records)

    out = run_photo_tagging_batch(
        bucket="b", source_prefix="photo_collection", db_url="",
        list_keys_fn=list_keys_fn, fetch_tagged_fn=fetch_tagged_fn,
        tag_fn=tag_fn, upsert_fn=upsert_fn,
    )
    assert out["untagged_processed"] == 2
    assert out["written"] == 2
    assert out["failed"] == 0
    assert out["providers"] and out["providers"][0] == "qwen"  # Qwen leads


def test_select_untagged_photo_keys_dedups_and_skips_tagged() -> None:
    keys = [
        "photo_collection/a/one.jpg",
        "photo_collection/b/one.jpg",   # same basename -> same photo id -> deduped
        "photo_collection/a/two.png",
        "photo_collection/a/three.webp",
    ]
    tagged = {photo_clip_id("two.png")}
    out = select_untagged_photo_keys(keys, tagged)
    # 'one' once (dedup), 'two' skipped (already tagged), 'three' kept
    assert out == ["photo_collection/a/one.jpg", "photo_collection/a/three.webp"]
