# -*- coding: utf-8 -*-
"""A persisted vibe shortlist must be re-ranked when the bucket catalog is re-cut.

The shortlist is the FULL ranked catalog, stored per chat. The old guard only
compared id PREFIXES, so it caught a shortlist from the wrong plane
("visual:" vs "photo:") and nothing else. Re-cutting the photo catalog keeps every
id under "photo:", so every chat that had ranked before the change kept passing
the guard: retired buckets stayed on the buttons and not one new bucket ever
appeared. Bucket sets are re-cut regularly, so the check has to be against the
catalog, and it has to hold in BOTH bots.
"""
from __future__ import annotations

import importlib
from typing import List

import pytest


BOTS = ("services.tg_bot_botapi.app", "services.tg_bot_public.app")


def _mod(name: str):
    return importlib.import_module(name)


def _photo_catalog_ids() -> List[str]:
    from mlcore.photo_bucket_catalog import load_photo_catalog

    return [b.bucket_id for b in load_photo_catalog()]


@pytest.mark.parametrize("bot", BOTS)
def test_the_live_catalog_is_kept(bot: str) -> None:
    app = _mod(bot)
    assert app._stale_vibe_shortlist_reason(_photo_catalog_ids(), "photo") == ""


@pytest.mark.parametrize("bot", BOTS)
def test_a_shortlist_missing_new_buckets_is_rejected(bot: str) -> None:
    """The exact regression: a stale list that is a strict SUBSET of the catalog.
    Every id is still well-formed and still under "photo:", so only a catalog
    comparison can see it."""
    app = _mod(bot)
    ids = _photo_catalog_ids()
    assert len(ids) > 1
    assert app._stale_vibe_shortlist_reason(ids[:-1], "photo") == "new_buckets"


@pytest.mark.parametrize("bot", BOTS)
def test_a_shortlist_holding_a_retired_bucket_is_rejected(bot: str) -> None:
    app = _mod(bot)
    ids = _photo_catalog_ids() + ["photo:some_retired_bucket"]
    assert app._stale_vibe_shortlist_reason(ids, "photo") == "retired_buckets"


@pytest.mark.parametrize("bot", BOTS)
def test_wrong_plane_and_legacy_ids_still_rejected(bot: str) -> None:
    app = _mod(bot)
    assert app._stale_vibe_shortlist_reason(["visual:urban_solitude_dark"], "photo")
    assert app._stale_vibe_shortlist_reason(["urban_minor:city_night"], "photo") == "legacy_ids"


@pytest.mark.parametrize("bot", BOTS)
def test_an_unreadable_catalog_keeps_the_stored_shortlist(
    bot: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No opinion must not mean "everything is stale" — that would re-rank every
    chat on every message."""
    app = _mod(bot)
    monkeypatch.setattr(app, "_live_bucket_ids", lambda _bg: set())
    assert app._stale_vibe_shortlist_reason(["photo:whatever_it_was"], "photo") == ""
    assert app._stale_vibe_shortlist_reason(["visual:x"], "photo") == "wrong_plane"


@pytest.mark.parametrize("bot", BOTS)
def test_empty_shortlist_is_not_a_reason(bot: str) -> None:
    app = _mod(bot)
    assert app._stale_vibe_shortlist_reason([], "photo") == ""
