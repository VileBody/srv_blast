from __future__ import annotations

from mlcore.footage_tagger import (
    TAGGER_VERSION,
    merge_frame_votes,
    parse_vision_json,
    record_from_votes,
    run_tagging_batch,
    select_untagged_keys,
)


def test_parse_vision_json_plain_and_fenced() -> None:
    assert parse_vision_json('{"mood": "minor"}') == {"mood": "minor"}
    assert parse_vision_json('```json\n{"mood": "major"}\n```') == {"mood": "major"}
    assert parse_vision_json("not json") is None


def test_merge_frame_votes_majority_and_tag_union() -> None:
    frames = [
        {"color_tone": "dark", "mood": "minor", "has_people": True, "people_type": "guys", "theme_tags": ["night", "rain"]},
        {"color_tone": "dark", "mood": "minor", "has_people": True, "people_type": "guys", "theme_tags": ["rain", "city"]},
        {"color_tone": "cold", "mood": "major", "has_people": False, "people_type": "none", "theme_tags": ["fog"]},
    ]
    merged = merge_frame_votes(frames)
    assert merged["color_tone"] == "dark"  # 2 vs 1
    assert merged["mood"] == "minor"
    assert merged["has_people"] is True  # 2 of 3
    assert merged["people_type"] == "guys"
    assert merged["theme_tags"] == ["rain", "night", "city", "fog"]  # repeated tags rank first


def test_select_untagged_keys_dedups_and_skips() -> None:
    keys = [
        "pinterest_collection/Rock/dark_forest/1001276929637034910_a.mp4",
        "pinterest_collection/Pop/dark_forest/1001276929637034910_a.mp4",  # same clip_id (dup folder)
        "pinterest_collection/Pop/x/2002222222222222222_b.mp4",
        "pinterest_collection/Pop/x/no_digits.mp4",  # unkeyable -> skipped
    ]
    tagged = {"2002222222222222222"}  # already tagged
    out = select_untagged_keys(keys, tagged)
    assert out == ["pinterest_collection/Rock/dark_forest/1001276929637034910_a.mp4"]


def test_record_from_votes_shapes_and_keys() -> None:
    rec = record_from_votes(
        s3_key="pinterest_collection/Rock/df/1001276929637034910_x.mp4",
        votes={"mood": "minor", "color_tone": "cold", "people_type": "none", "theme_tags": ["Fog", "fog", "night"]},
    )
    assert rec is not None
    assert rec["clip_id"] == "1001276929637034910"
    assert rec["theme_tags"] == ["fog", "night"]  # normalized + deduped
    assert rec["tagger"] == TAGGER_VERSION


def test_run_tagging_batch_with_injected_io() -> None:
    all_keys = [
        "p/Rock/a/1111111111111111111_x.mp4",
        "p/Pop/a/1111111111111111111_x.mp4",  # dup clip_id
        "p/Pop/b/2222222222222222222_y.mp4",
        "p/Pop/c/3333333333333333333_z.mp4",
    ]
    tagged_ids = {"3333333333333333333"}  # already done
    upserted: list = []

    def fake_tag(key: str):
        cid = key.split("/")[-1].split("_")[0]
        return {"clip_id": cid, "file_name": key.split("/")[-1], "s3_key": key,
                "video_key": key.split("/")[-1], "mood": "minor", "color_tone": "cold",
                "people_type": "none", "theme_tags": ["night"], "tagger": "qwen"}

    progress: list = []
    summary = run_tagging_batch(
        bucket="b",
        source_prefix="p",
        db_url="x",
        flush_every=1,
        progress_cb=lambda d, t, w: progress.append((d, t, w)),
        list_keys_fn=lambda: all_keys,
        fetch_tagged_fn=lambda: tagged_ids,
        tag_fn=fake_tag,
        upsert_fn=lambda recs: (upserted.extend(recs) or len(recs)),
    )
    # 2 unique untagged clips (1111..., 2222...); 3333 already tagged, dup folder collapsed
    assert summary["untagged_processed"] == 2
    assert summary["written"] == 2
    assert summary["failed"] == 0
    assert {r["clip_id"] for r in upserted} == {"1111111111111111111", "2222222222222222222"}
    assert progress[-1] == (2, 2, 2)


def test_run_tagging_batch_counts_failures() -> None:
    summary = run_tagging_batch(
        bucket="b", source_prefix="p", db_url="x",
        list_keys_fn=lambda: ["p/a/1111111111111111111_x.mp4"],
        fetch_tagged_fn=lambda: set(),
        tag_fn=lambda key: None,  # tagging fails
        upsert_fn=lambda recs: len(recs),
    )
    assert summary["untagged_processed"] == 1
    assert summary["written"] == 0
    assert summary["failed"] == 1
