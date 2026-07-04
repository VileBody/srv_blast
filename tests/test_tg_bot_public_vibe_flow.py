# -*- coding: utf-8 -*-
"""Parity test: tg_bot_public mirrors the footage precision (vibe) flow of
tg_bot_botapi (Phase 2b).

The ranked-shortlist vibe multi-select UX is now ported 1:1 into the public bot
(genre/artist → vibe reroute, paged inline picker, enqueue bucket distribution,
auto-cursor removal). These tests pin both the data layer (stage + ChatState
vibe_* fields + OrchestratorClient.rank_buckets wiring) and the behaviour
(shortlist → multi-select → done → enqueue distribution), and assert the one
required difference: previews are sent via the `file_id_public` variant.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect


# --------------------------------------------------------------------------- #
# Data-layer parity                                                            #
# --------------------------------------------------------------------------- #
def test_vibe_stage_strings_match_across_bots():
    from services.tg_bot_botapi.state_store import STAGE_WAIT_VIBE as team_stage
    from services.tg_bot_public.state_store import STAGE_WAIT_VIBE as pub_stage

    assert team_stage == pub_stage == "WAIT_VIBE"


def test_vibe_stage_in_public_vibe_stages_frozenset():
    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import STAGE_WAIT_VIBE

    assert STAGE_WAIT_VIBE in pub.VIBE_STAGES


def test_chatstate_vibe_fields_default_consistent_across_bots():
    from services.tg_bot_botapi.state_store import ChatState as TeamState
    from services.tg_bot_public.state_store import ChatState as PubState

    team = TeamState(chat_id=1)
    pub = PubState(chat_id=1)
    for st in (team, pub):
        assert st.vibe_ranked_ids == []
        assert st.vibe_labels_by_id == {}
        assert st.vibe_page == 0
        assert st.vibe_selected_ids == []
        assert st.vibe_rank_status == ""


def test_chatstate_vibe_fields_roundtrip():
    """A public chat state carrying mirrored vibe_* values must round-trip
    through JSON (so a chat pre-populated by a roll-forward survives reload)."""
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(
        chat_id=7,
        vibe_ranked_ids=["heartbreak_minor:eerie_nature", "love_major:warm_sun"],
        vibe_labels_by_id={"heartbreak_minor:eerie_nature": "Тревожная природа"},
        vibe_page=1,
        vibe_selected_ids=["heartbreak_minor:eerie_nature"],
        vibe_rank_status="ready",
    )
    again = ChatState.model_validate_json(st.model_dump_json())
    assert again.vibe_ranked_ids == st.vibe_ranked_ids
    assert again.vibe_labels_by_id == st.vibe_labels_by_id
    assert again.vibe_page == 1
    assert again.vibe_selected_ids == st.vibe_selected_ids
    assert again.vibe_rank_status == "ready"


def test_orchestrator_client_rank_buckets_signature_parity():
    from services.tg_bot_botapi.orchestrator_client import (
        OrchestratorClient as TeamClient,
    )
    from services.tg_bot_public.orchestrator_client import (
        OrchestratorClient as PubClient,
    )

    team_sig = inspect.signature(TeamClient.rank_buckets)
    pub_sig = inspect.signature(PubClient.rank_buckets)
    assert set(team_sig.parameters) == set(pub_sig.parameters)
    for name in ("lyrics", "mood", "top"):
        assert name in pub_sig.parameters


def test_vibe_methods_present_in_public_with_team_signatures():
    """Every ported vibe method exists in public with the same signature."""
    from services.tg_bot_botapi.app import BlastBotApp as Team
    from services.tg_bot_public.app import BlastBotApp as Pub

    methods = [
        "_ask_vibe_shortlist",
        "_build_vibe_keyboard",
        "_vibe_shortlist_text",
        "_vibe_page_count",
        "_handle_vibe_callback",
        "_send_vibe_previews",
        "_parse_ranked_buckets",
        "_ensure_vibe_ranked",
        "_trigger_vibe_ranker_task",
        "_run_vibe_ranker_bg",
        "_handle_wait_vibe_text",
    ]
    for name in methods:
        assert hasattr(Pub, name), f"public bot missing {name}"
        assert inspect.signature(getattr(Pub, name)) == inspect.signature(getattr(Team, name)), name


def test_vibe_callback_prefix_and_controls_match_across_bots():
    from services.tg_bot_botapi import app as team
    from services.tg_bot_public import app as pub

    assert pub.VIBE_CB_PREFIX == team.VIBE_CB_PREFIX == "vibe:"
    assert pub.VIBE_PAGE_SIZE == team.VIBE_PAGE_SIZE
    # Paging button reads as "show more options", NOT a reload — the "🔄 Обновить"
    # copy was misread by users as re-rank/reset. Pin the literal so both bots
    # stay in sync on the label.
    assert pub.BTN_VIBE_REFRESH == team.BTN_VIBE_REFRESH == "Ещё варианты ›"
    assert pub.BTN_VIBE_DONE == team.BTN_VIBE_DONE
    assert pub.BTN_VIBE_AUTO == team.BTN_VIBE_AUTO


def test_public_preview_uses_file_id_public_field():
    """The one mandatory difference vs team: previews resolve file_id_public."""
    from services.tg_bot_public import app as pub
    from services.tg_bot_botapi import app as team

    assert pub._BUCKET_PREVIEW_FILE_ID_FIELD == "file_id_public"
    assert team._BUCKET_PREVIEW_FILE_ID_FIELD == "file_id"


def test_public_vibe_flow_flag_default_on(monkeypatch):
    """Default-on in both bots now that the UX is ported. With an explicit "0"
    the routing guard falls back to the legacy genre/artist picker."""
    monkeypatch.delenv("FOOTAGE_VIBE_FLOW_ENABLED", raising=False)
    from services.tg_bot_public import app as pub
    pub = importlib.reload(pub)
    assert pub.FOOTAGE_VIBE_FLOW_ENABLED is True

    from services.tg_bot_public.state_store import ChatState, STAGE_WAIT_VIBE
    st = ChatState(chat_id=1, stage=STAGE_WAIT_VIBE)
    assert pub._should_route_to_vibe_flow(st) is True

    monkeypatch.setenv("FOOTAGE_VIBE_FLOW_ENABLED", "0")
    pub = importlib.reload(pub)
    assert pub.FOOTAGE_VIBE_FLOW_ENABLED is False
    st = ChatState(chat_id=1, stage=STAGE_WAIT_VIBE)
    assert pub._should_route_to_vibe_flow(st) is False
    # restore default-on module for the rest of the session
    monkeypatch.delenv("FOOTAGE_VIBE_FLOW_ENABLED", raising=False)
    importlib.reload(pub)


# --------------------------------------------------------------------------- #
# Behavioural parity (drives the real public handlers with in-process fakes)   #
# --------------------------------------------------------------------------- #
def _make_app(monkeypatch, ranked):
    monkeypatch.setenv("FOOTAGE_VIBE_FLOW_ENABLED", "1")
    from services.tg_bot_public import app as pub
    pub = importlib.reload(pub)

    class _Store:
        def __init__(self):
            self.by_id = {}

        async def get(self, chat_id):
            from services.tg_bot_public.state_store import ChatState
            return self.by_id.get(int(chat_id)) or ChatState(chat_id=int(chat_id))

        async def set(self, st):
            self.by_id[int(st.chat_id)] = st

    class _Orchestrator:
        def __init__(self):
            self.rank_calls = 0

        async def rank_buckets(self, *, lyrics, mood="", top=0):
            self.rank_calls += 1
            return {
                "buckets": [
                    {"bucket_id": b, "theme": b.split(":")[0],
                     "tags_group": b.split(":")[1], "mood": "minor", "label": b.split(":")[1]}
                    for b in ranked
                ],
                "used_llm": True,
            }

    app = pub.BlastBotApp.__new__(pub.BlastBotApp)
    app.store = _Store()
    app.orchestrator = _Orchestrator()
    return pub, app


class _Msg:
    def __init__(self, chat_id=7, text=""):
        self.text = text
        self._chat_id = chat_id
        self.answers = []

        class _Chat:
            id = chat_id
        self.chat = _Chat()

    async def answer(self, text="", reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return self

    async def answer_video(self, *args, **kwargs):
        self.answers.append(("video", kwargs.get("video")))
        return self

    async def edit_text(self, text="", reply_markup=None):
        self.answers.append(("edit", text, reply_markup))
        return self

    async def edit_reply_markup(self, reply_markup=None):
        self.answers.append(("edit_markup", reply_markup))
        return self


class _CB:
    def __init__(self, data, chat_id=7):
        self.data = data
        self.message = _Msg(chat_id=chat_id)
        self.answers = []

    async def answer(self, text="", show_alert=False):
        self.answers.append(text)


def test_vibe_flow_end_to_end(monkeypatch):
    ranked = ["t0:g0", "t1:g1", "t2:g2", "t3:g3"]  # 4 buckets → 2 pages of 3+1
    pub, app = _make_app(monkeypatch, ranked)
    from services.tg_bot_public.state_store import (
        ChatState, STAGE_WAIT_VIBE, STAGE_WAIT_SUBTITLES_MODE,
    )

    async def _run():
        st = ChatState(chat_id=7, lyrics_text="some lyrics", bg_mode="footage")
        await app.store.set(st)

        # 1) Enter the shortlist: ranks (sync fallback) + parks on WAIT_VIBE.
        await app._ask_vibe_shortlist(_Msg(), st)
        st = await app.store.get(7)
        assert st.stage == STAGE_WAIT_VIBE
        assert st.vibe_ranked_ids == ranked
        assert st.footage_artist_id == ""

        # 2) Toggle bucket 0 (page 0) → selected.
        await app._handle_vibe_callback(_CB("vibe:tog:0"))
        st = await app.store.get(7)
        assert st.vibe_selected_ids == ["t0:g0"]

        # 3) Next page, then toggle bucket 3 → selection PERSISTS across pages.
        await app._handle_vibe_callback(_CB("vibe:more"))
        st = await app.store.get(7)
        assert st.vibe_page == 1
        await app._handle_vibe_callback(_CB("vibe:tog:3"))
        st = await app.store.get(7)
        assert st.vibe_selected_ids == ["t0:g0", "t3:g3"]

        # 4) Done → advances to the subtitles step.
        await app._handle_vibe_callback(_CB("vibe:done"))
        st = await app.store.get(7)
        assert st.stage == STAGE_WAIT_SUBTITLES_MODE

        # 5) Enqueue distribution: video[i]=selected[(offset-1) % K], round-robin.
        v1 = await app._resolve_rotation_slot_for_enqueue(st=st, offset=1)
        v2 = await app._resolve_rotation_slot_for_enqueue(st=st, offset=2)
        v3 = await app._resolve_rotation_slot_for_enqueue(st=st, offset=3)
        assert v1[:2] == ("t0", "g0")
        assert v2[:2] == ("t3", "g3")
        assert v3[:2] == ("t0", "g0")  # wraps round-robin

    asyncio.run(_run())


def test_vibe_done_requires_selection(monkeypatch):
    ranked = ["t0:g0", "t1:g1"]
    pub, app = _make_app(monkeypatch, ranked)
    from services.tg_bot_public.state_store import ChatState, STAGE_WAIT_VIBE

    async def _run():
        st = ChatState(chat_id=7, lyrics_text="x", bg_mode="footage")
        await app.store.set(st)
        await app._ask_vibe_shortlist(_Msg(), st)
        cb = _CB("vibe:done")
        await app._handle_vibe_callback(cb)
        st = await app.store.get(7)
        assert st.stage == STAGE_WAIT_VIBE
        assert st.vibe_selected_ids == []

    asyncio.run(_run())


def test_ensure_vibe_ranked_retries_transient_failures(monkeypatch):
    """Regression: a single client-side hiccup (timeout/connection reset) on
    the sync rank call must NOT permanently strand the chat in the legacy
    genre/artist picker. The server endpoint never 500s/empties, so a failure
    here is transient — retry before giving up."""
    pub, app = _make_app(monkeypatch, ranked=["t0:g0"])
    monkeypatch.setattr(pub.asyncio, "sleep", lambda *_a, **_k: _immediate())

    async def _immediate():
        return None

    calls = {"n": 0}
    real_rank_buckets = app.orchestrator.rank_buckets

    async def _flaky_rank_buckets(*, lyrics, mood="", top=0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("simulated transient failure")
        return await real_rank_buckets(lyrics=lyrics, mood=mood, top=top)

    app.orchestrator.rank_buckets = _flaky_rank_buckets

    from services.tg_bot_public.state_store import ChatState

    async def _run():
        st = ChatState(chat_id=7, lyrics_text="x", bg_mode="footage")
        ok = await app._ensure_vibe_ranked(st)
        assert ok is True
        assert calls["n"] == 3
        assert st.vibe_ranked_ids == ["t0:g0"]

    asyncio.run(_run())


def test_ensure_vibe_ranked_gives_up_after_exhausting_retries(monkeypatch):
    """After retries are exhausted, still falls back cleanly (caller routes to
    the legacy genre picker) rather than raising."""
    pub, app = _make_app(monkeypatch, ranked=["t0:g0"])
    monkeypatch.setattr(pub.asyncio, "sleep", lambda *_a, **_k: _immediate())

    async def _immediate():
        return None

    async def _always_fails(*, lyrics, mood="", top=0):
        raise ConnectionError("simulated persistent failure")

    app.orchestrator.rank_buckets = _always_fails

    from services.tg_bot_public.state_store import ChatState

    async def _run():
        st = ChatState(chat_id=7, lyrics_text="x", bg_mode="footage")
        ok = await app._ensure_vibe_ranked(st)
        assert ok is False
        assert st.vibe_ranked_ids == []

    asyncio.run(_run())


def test_vibe_auto_picks_top1(monkeypatch):
    ranked = ["t0:g0", "t1:g1", "t2:g2"]
    pub, app = _make_app(monkeypatch, ranked)
    from services.tg_bot_public.state_store import ChatState, STAGE_WAIT_SUBTITLES_MODE

    async def _run():
        st = ChatState(chat_id=7, lyrics_text="x", bg_mode="footage")
        await app.store.set(st)
        await app._ask_vibe_shortlist(_Msg(), st)
        await app._handle_vibe_callback(_CB("vibe:auto"))
        st = await app.store.get(7)
        assert st.vibe_selected_ids == ["t0:g0"]
        assert st.stage == STAGE_WAIT_SUBTITLES_MODE
        slot = await app._resolve_rotation_slot_for_enqueue(st=st, offset=1)
        assert slot[:2] == ("t0", "g0")

    asyncio.run(_run())
