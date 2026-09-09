# -*- coding: utf-8 -*-
"""A collection bucket must read its preview from the COLLECTION store.

Each plane keeps its own previews file. The lookup only knew "photo" vs
"video", so a `collection:` id fell through to the footage store and found
nothing — quietly. The shortlist would have shown film buttons with no reel
beside them, and the build side would have looked fine: the previews exist, in a
file nobody reads.

Caught before commissioning the preview build, not after.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

BOTS = ("services.tg_bot_botapi.app", "services.tg_bot_public.app")


def _mod(name: str):
    return importlib.import_module(name)


@pytest.mark.parametrize("bot", BOTS)
def test_each_plane_has_its_own_store_file(bot: str) -> None:
    app = _mod(bot)
    paths = {mt: app._bucket_previews_path(mt) for mt in ("video", "photo", "collection")}
    assert len({p.name for p in paths.values()}) == 3
    assert paths["collection"].name == "collection_bucket_previews.json"


@pytest.mark.parametrize("bot", BOTS)
def test_a_collection_id_reads_the_collection_store(
    bot: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _mod(bot)
    field = app._BUCKET_PREVIEW_FILE_ID_FIELD
    store = {"previews": {"collection:films__брат": {field: "FILEID_FILM"}}}
    path = tmp_path / "collection_bucket_previews.json"
    path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        app, "_bucket_previews_path",
        lambda mt="video": path if mt == "collection" else tmp_path / "other.json",
    )
    monkeypatch.setattr(app, "_BUCKET_PREVIEWS_CACHE", None, raising=False)
    monkeypatch.setattr(app, "_PHOTO_BUCKET_PREVIEWS_CACHE", None, raising=False)
    monkeypatch.setattr(app, "_COLLECTION_BUCKET_PREVIEWS_CACHE", None, raising=False)

    assert app._bucket_preview_file_id("collection:films__брат") == "FILEID_FILM"


@pytest.mark.parametrize("bot", BOTS)
def test_the_prefix_decides_the_store(bot: str) -> None:
    # Pure routing: an id from one plane must never be looked up in another's file.
    src = __import__("inspect").getsource(_mod(bot)._bucket_preview_file_id)
    assert 'bid.startswith("collection:")' in src
    assert 'bid.startswith("photo:")' in src


@pytest.mark.parametrize("bot", BOTS)
def test_a_missing_store_is_silent_not_fatal(
    bot: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Previews are optional: a bucket without one still renders as a button.
    app = _mod(bot)
    monkeypatch.setattr(app, "_bucket_previews_path", lambda mt="video": tmp_path / "nope.json")
    monkeypatch.setattr(app, "_COLLECTION_BUCKET_PREVIEWS_CACHE", None, raising=False)
    assert app._bucket_preview_file_id("collection:films__брат") == ""
