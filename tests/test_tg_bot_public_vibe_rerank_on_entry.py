# -*- coding: utf-8 -*-
"""Entering the vibe step re-ranks; a stored shortlist is only a fallback.

The staleness guard compares the SET of buckets in a stored shortlist against the
live catalog. That catches a re-cut catalog, but it is blind to a re-ORDERING —
and improving the ranking is precisely a re-ordering, same buckets, better order.
So after the photo ranking fix every chat that had already ranked kept serving
its old order, and nothing else re-triggers ranking until new lyrics arrive.

Ranking is deterministic and LLM-free by default, so the step now re-ranks on
entry instead of trying to detect that the ranking logic changed — detection is
what failed here, twice.
"""
from __future__ import annotations

import importlib
import inspect
import re

import pytest


BOTS = ("services.tg_bot_botapi.app", "services.tg_bot_public.app")


def _src(bot: str, name: str) -> str:
    mod = importlib.import_module(bot)
    return inspect.getsource(getattr(mod.BlastBotApp, name))


@pytest.mark.parametrize("bot", BOTS)
def test_the_step_entry_forces_a_rerank(bot: str) -> None:
    src = _src(bot, "_ask_vibe_shortlist")
    assert "_ensure_vibe_ranked(st, force=True)" in src, src


@pytest.mark.parametrize("bot", BOTS)
def test_force_skips_the_stored_shortlist_shortcut(bot: str) -> None:
    """The early return is what pinned the old order in place."""
    src = _src(bot, "_ensure_vibe_ranked")
    assert re.search(r"if st\.vibe_ranked_ids and not force:", src), src
    # the early return must live under that guard, not before it
    assert src.index("and not force:") < src.index("return True")


@pytest.mark.parametrize("bot", BOTS)
def test_a_failed_refresh_keeps_the_stored_shortlist(bot: str) -> None:
    """A network hiccup must not cost the user their shortlist and drop them into
    the legacy genre picker — an older order still beats no order."""
    src = _src(bot, "_ensure_vibe_ranked")
    assert "stored_ids = list(st.vibe_ranked_ids or [])" in src
    assert "keeping_stored" in src
    # ...but a stored list that is genuinely stale must NOT be resurrected
    assert "if stored_ids and not _stale_vibe_shortlist_reason(stored_ids, st.bg_mode):" in src


@pytest.mark.parametrize("bot", BOTS)
def test_paging_does_not_re_rank(bot: str) -> None:
    """Only entry forces it; re-ranking on every page turn would add a round trip
    to each tap for a result that is deterministic anyway."""
    src = _src(bot, "_handle_wait_vibe_text")
    assert "force=True" not in src
