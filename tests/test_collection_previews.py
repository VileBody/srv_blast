"""Example reels for collection buckets."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from mlcore import footage_bucket_previews as bp
from mlcore.footage_collection_catalog import CollectionBucket

_BUCKET = CollectionBucket(
    slug="films__interstellar",
    label="Интерстеллар",
    kind="films",
    folder="interstellar",
    description="космос, масштаб, холодный свет",
)


def _asset(name: str, genre: str, tag: str) -> Dict[str, Any]:
    return {
        "file_name": name,
        "genre": genre,
        "tag": tag,
        "duration_sec": 20.0,
        "src_w": 1920,
        "src_h": 1080,
    }


@pytest.fixture()
def registry(tmp_path, monkeypatch: pytest.MonkeyPatch):
    import json

    p = tmp_path / "footage_collections.json"
    p.write_text(
        json.dumps(
            {
                "collections": [
                    {
                        "kind": "films",
                        "folder": "interstellar",
                        "label": "Интерстеллар",
                        "description": "космос, масштаб, холодный свет",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FOOTAGE_COLLECTIONS_JSON", str(p))


def test_preview_draws_only_from_the_collection_folder(registry) -> None:
    assets: List[Dict[str, Any]] = [
        _asset("a.mp4", "films", "interstellar"),
        _asset("b.mp4", "films", "interstellar"),
        _asset("other.mp4", "films", "dune"),
        _asset("vibe.mp4", "pop", "sad"),
    ]
    got = bp.select_bucket_clips(_BUCKET, assets, seed="s", top_n=5, media_type="collection")
    assert sorted(c["file_name"] for c in got) == ["a.mp4", "b.mp4"]


def test_selection_is_deterministic(registry) -> None:
    assets = [_asset(f"{i}.mp4", "films", "interstellar") for i in range(10)]
    first = bp.select_bucket_clips(_BUCKET, assets, seed="s", top_n=5, media_type="collection")
    second = bp.select_bucket_clips(_BUCKET, assets, seed="s", top_n=5, media_type="collection")
    assert [c["file_name"] for c in first] == [c["file_name"] for c in second]


def test_description_uses_the_operator_line_not_the_identity_sentinel() -> None:
    # priority_tags holds "collection films interstellar" — never show that.
    got = bp.build_bucket_description(_BUCKET)
    assert got == "космос, масштаб, холодный свет"
    assert "collection" not in got


def test_description_falls_back_to_the_label() -> None:
    bare = CollectionBucket(
        slug="films__dune", label="Дюна", kind="films", folder="dune"
    )
    assert bp.build_bucket_description(bare) == "Дюна"


@pytest.mark.parametrize(
    "preset, expected",
    [("wide", (1920, 1080)), ("square", (1080, 1080)), ("vertical", (1080, 1920))],
)
def test_preview_renders_in_the_delivery_geometry(preset: str, expected: tuple) -> None:
    # A film previewed as a 9:16 reel and delivered as 16:9 misrepresents the
    # product, so the preview borrows the render preset registry.
    spec = bp.build_collection_montage_spec(
        _BUCKET, [{"file_name": "a.mp4"}], render_preset=preset
    )
    assert (spec["width"], spec["height"]) == expected


def test_montage_spec_carries_the_clips_and_label() -> None:
    spec = bp.build_collection_montage_spec(
        _BUCKET, [{"file_name": "a.mp4"}, {"file_name": "b.mp4"}]
    )
    assert [c["relpath"] for c in spec["clips"]] == [
        "media/video/a.mp4",
        "media/video/b.mp4",
    ]
    assert spec["label"] == "Интерстеллар"


def test_collections_keep_their_own_previews_store() -> None:
    # Mixing them into the vibe store would let a shortlist serve the wrong shape.
    assert bp.DEFAULT_COLLECTION_PREVIEWS_PATH != bp.DEFAULT_PREVIEWS_PATH
    assert bp.DEFAULT_COLLECTION_PREVIEWS_PATH != bp.DEFAULT_PHOTO_PREVIEWS_PATH
