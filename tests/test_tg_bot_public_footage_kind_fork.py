# -*- coding: utf-8 -*-
"""The footage fork: which plane the vibe shortlist draws from.

After «Футажи» the user now picks between the semantic 9:16 vibes and a film
collection. The two catalogs share no bucket ids, live in different inventories
and reach the picker through different code paths, so the choice has to travel
with the chat — and it has to exist in BOTH bots or the parity gate breaks the
public one the next time the team bot moves.

Only two options are live on purpose. The catalog also carries 16:9 and
«Личности» kinds, but offering a button for a pool nobody has filled would
strand the user on an empty shortlist.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

BOTS = ("services.tg_bot_botapi.app", "services.tg_bot_public.app")


def _mod(name: str):
    return importlib.import_module(name)


def _src(mod, attr: str) -> str:
    """Source of a bot METHOD — both bots expose the flow on BlastBotApp."""
    return inspect.getsource(getattr(mod.BlastBotApp, attr))


# --------------------------------------------------------------------------- #
# both bots carry the same fork
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bot", BOTS)
def test_the_stage_and_state_field_exist(bot: str) -> None:
    app = _mod(bot)
    store = importlib.import_module(bot.rsplit(".", 1)[0] + ".state_store")
    assert store.STAGE_WAIT_FOOTAGE_KIND == "WAIT_FOOTAGE_KIND"
    assert store.ChatState(chat_id=1).footage_kind == app.FOOTAGE_KIND_VERTICAL


@pytest.mark.parametrize("bot", BOTS)
def test_exactly_two_options_are_offered(bot: str) -> None:
    app = _mod(bot)
    src = _src(app, "_ask_footage_kind")
    keyboard = src.split("reply_markup=", 1)[1]
    assert "BTN_FOOTAGE_KIND_VERTICAL" in keyboard
    assert "BTN_FOOTAGE_KIND_FILMS" in keyboard
    # The unfilled kinds must not be reachable yet — the docstring may
    # name them, the keyboard may not.
    assert "Личности" not in keyboard
    assert "cine16x9" not in keyboard
    assert app.BTN_FOOTAGE_KIND_VERTICAL == "9:16"
    assert app.BTN_FOOTAGE_KIND_FILMS == "Фильмы"


@pytest.mark.parametrize("bot", BOTS)
def test_the_handler_accepts_both_and_rejects_anything_else(bot: str) -> None:
    src = _src(_mod(bot), "_handle_wait_footage_kind")
    assert "FOOTAGE_KIND_VERTICAL" in src
    assert "FOOTAGE_KIND_FILMS" in src
    assert "await self._ask_vibe_shortlist(message, st)" in src


@pytest.mark.parametrize("bot", BOTS)
def test_switching_planes_drops_the_old_shortlist(bot: str) -> None:
    # The two catalogs share no ids, so a kept shortlist would be entirely stale
    # and every button on it would resolve to a bucket the new plane lacks.
    src = _src(_mod(bot), "_handle_wait_footage_kind")
    assert "st.vibe_ranked_ids = []" in src
    assert "st.vibe_selected_ids = []" in src


@pytest.mark.parametrize("bot", BOTS)
def test_the_stage_is_dispatched(bot: str) -> None:
    # A stage nothing routes to is a dead end: the user taps and nothing happens.
    src = inspect.getsource(_mod(bot))
    assert "if st.stage == STAGE_WAIT_FOOTAGE_KIND:" in src
    assert "await self._handle_wait_footage_kind(message, st)" in src


# --------------------------------------------------------------------------- #
# the plane reaches the ranker and the staleness check
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bot", BOTS)
def test_kind_maps_to_the_ranker_pool(bot: str) -> None:
    app = _mod(bot)
    assert app._pool_for_footage_kind(app.FOOTAGE_KIND_VERTICAL) == "vibes"
    assert app._pool_for_footage_kind(app.FOOTAGE_KIND_FILMS) == "films"
    # An unknown kind must fall back to the vibes catalog, never to a collection
    # pool the chat did not choose.
    assert app._pool_for_footage_kind("") == "vibes"
    assert app._pool_for_footage_kind("nonsense") == "vibes"


@pytest.mark.parametrize("bot", BOTS)
def test_the_ranker_is_asked_for_the_chosen_pool(bot: str) -> None:
    src = _src(_mod(bot), "_ensure_vibe_ranked")
    assert "pool=_pool_for_footage_kind(st.footage_kind)" in src


@pytest.mark.parametrize("bot", BOTS)
def test_the_background_ranker_will_not_overwrite_another_plane(bot: str) -> None:
    # It starts when the lyrics arrive, before the plane is chosen; landing on a
    # shortlist for the plane the user has since picked would replace it wholesale.
    src = _src(_mod(bot), "_run_vibe_ranker_bg")
    assert "footage_kind" in src
    assert "return" in src


@pytest.mark.parametrize("bot", BOTS)
def test_films_shortlist_is_validated_against_the_collection_catalog(bot: str) -> None:
    app = _mod(bot)
    live = app._live_bucket_ids("footage", app.FOOTAGE_KIND_FILMS)
    assert live, "the shipped registry should expose film collections"
    assert all(b.startswith("collection:") for b in live)
    # And it must not be confused with the vibe catalog.
    assert not (live & app._live_bucket_ids("footage", app.FOOTAGE_KIND_VERTICAL))


@pytest.mark.parametrize("bot", BOTS)
def test_a_vibe_shortlist_is_stale_on_the_films_fork(bot: str) -> None:
    app = _mod(bot)
    assert app._stale_vibe_shortlist_reason(
        ["visual:anything"], "footage", app.FOOTAGE_KIND_FILMS
    )


@pytest.mark.parametrize("bot", BOTS)
def test_the_client_forwards_the_pool(bot: str) -> None:
    client = importlib.import_module(bot.rsplit(".", 1)[0] + ".orchestrator_client")
    sig = inspect.signature(client.OrchestratorClient.rank_buckets)
    assert "pool" in sig.parameters
    assert sig.parameters["pool"].default == "vibes"


# --------------------------------------------------------------------------- #
# what the fork hands the backend
# --------------------------------------------------------------------------- #
def test_a_film_bucket_id_resolves_to_the_collection_plane() -> None:
    # This is the whole contract with the backend: the id the bot ships becomes
    # rotation_theme="collection", which is what routes the job to the collection
    # inventory instead of the tagged pool.
    from mlcore.footage_batch_distribution import resolve_bucket_slot
    from mlcore.footage_collection_catalog import load_collection_catalog

    for bucket in load_collection_catalog():
        theme, group = resolve_bucket_slot(bucket.bucket_id, catalog=[])
        assert theme == "collection"
        assert group == bucket.slug


def test_films_render_vertically() -> None:
    # Frames for other aspect ratios come later as their own step; until then a
    # film job must not request a geometry the hook guard would also reject.
    from mlcore.footage_collection_catalog import load_collection_catalog

    for bucket in load_collection_catalog():
        assert bucket.formats == ("vertical",), bucket.slug
        assert bucket.default_format == "vertical"
