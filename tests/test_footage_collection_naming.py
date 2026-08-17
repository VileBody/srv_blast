"""Identity for collection clips: unique per folder, ASCII, stable."""
from __future__ import annotations

import pytest

from mlcore.footage_collection_naming import (
    ascii_slug,
    collection_clip_id,
    folder_token,
    qualified_file_name,
)

# The real batch: every folder shipped clip_001.mp4 … clip_0NN.mp4.
FOLDERS = [
    "бойцовский клуб", "брат", "бумер", "великий гэтсби",
    "волк с уолл-стрит", "до встречи с тобой", "дом gucci",
    "дьявол носит прада", "жмурки", "проект х",
    "реквием по мечте", "токийский дрифт",
]


def test_same_basename_in_every_folder_stays_distinct() -> None:
    # This is the bug that collapsed 939 files to 120.
    names = {qualified_file_name("films", f, "clip_003.mp4") for f in FOLDERS}
    assert len(names) == len(FOLDERS)


def test_whole_batch_is_collision_free() -> None:
    names = [
        qualified_file_name("films", f, f"clip_{i:03d}.mp4")
        for f in FOLDERS
        for i in range(1, 80)
    ]
    assert len(set(names)) == len(names)


def test_names_are_ascii_and_keep_the_extension() -> None:
    # AE fails on non-ASCII local paths and keys behaviour off the extension.
    got = qualified_file_name("films", "бойцовский клуб", "clip_003.mp4")
    got.encode("ascii")  # raises if any Cyrillic survived
    assert got.endswith(".mp4")
    assert "boycovskiy-klub" in got


def test_name_is_stable_across_calls() -> None:
    a = qualified_file_name("films", "токийский дрифт", "clip_012.mp4")
    b = qualified_file_name("films", "токийский дрифт", "clip_012.mp4")
    assert a == b


@pytest.mark.parametrize(
    "a, b",
    [
        ("бойцовский клуб", "Бойцовский Клуб"),   # S3 keys are case-sensitive
        ("проект х", "проект  х"),                 # punctuation/spacing
        ("дом gucci", "дом-gucci"),
    ],
)
def test_folders_a_slug_would_merge_stay_separate(a: str, b: str) -> None:
    # The unconditional hash is what protects against this, not the slug.
    assert folder_token("films", a) != folder_token("films", b)


def test_unslugifiable_folder_still_gets_a_token() -> None:
    token = folder_token("films", "东京漂移")
    assert token
    token.encode("ascii")


def test_clip_id_survives_names_without_an_eight_digit_run() -> None:
    # The shared extractor returns None for these, so every registry row was
    # dropped and the pool registry came back empty.
    cid = collection_clip_id("films", "бумер", "clip_003.mp4")
    assert cid
    cid.encode("ascii")


def test_clip_id_is_unique_per_folder_and_stable() -> None:
    ids = [collection_clip_id("films", f, "clip_003.mp4") for f in FOLDERS]
    assert len(set(ids)) == len(FOLDERS)
    assert collection_clip_id("films", "брат", "clip_1.mp4") == collection_clip_id(
        "films", "брат", "clip_1.mp4"
    )


def test_clip_id_ignores_the_extension_only() -> None:
    # Same clip, different container -> same logical clip is NOT assumed; the
    # stem is what identifies it, so .mp4 and .mov of one stem collide by design.
    assert collection_clip_id("films", "брат", "clip_1.mp4") == collection_clip_id(
        "films", "брат", "clip_1.mov"
    )


def test_ascii_slug_drops_nothing_meaningful_for_latin_input() -> None:
    assert ascii_slug("Wolf of Wall Street") == "wolf-of-wall-street"
    assert ascii_slug("dom GUCCI") == "dom-gucci"
