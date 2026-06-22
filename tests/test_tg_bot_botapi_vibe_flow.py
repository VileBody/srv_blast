# -*- coding: utf-8 -*-
"""Functional test for the team-bot footage precision (vibe) flow, Phase 2b.

Drives the real handlers with in-process fakes (no Telegram, no Redis, no
orchestrator HTTP) to de-risk the bot surgery:
  - ranker bg result lands in ChatState as the ranked shortlist
  - the inline multi-select toggles persist across "Обновить" pages
  - "Готово" finalizes, clears artist_id, and advances to the subtitles step
  - enqueue distributes the selected buckets round-robin (exact-slot per video)
"""
from __future__ import annotations

import asyncio


def _make_app(monkeypatch, ranked):
    from services.tg_bot_botapi import app as team

    monkeypatch.setenv("FOOTAGE_VIBE_FLOW_ENABLED", "1")

    class _Store:
        def __init__(self):
            self.by_id = {}

        async def get(self, chat_id):
            from services.tg_bot_botapi.state_store import ChatState
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

    app = team.BlastBotApp.__new__(team.BlastBotApp)
    app.store = _Store()
    app.orchestrator = _Orchestrator()
    return team, app


class _Msg:
    def __init__(self, chat_id=7, text=""):
        self.text = text
        self._chat_id = chat_id
        self.answers = []

        class _Chat:
            id = chat_id
        self.chat = _Chat()

    async def answer(self, text="", reply_markup=None):
        self.answers.append((text, reply_markup))
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
    team, app = _make_app(monkeypatch, ranked)
    from services.tg_bot_botapi.state_store import (
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

        # 5) Enqueue distribution: video[i]=selected[(i)] (offset is 1-based
        #    version_index → offset-1). 2 selected, round-robin.
        v1 = await app._resolve_rotation_slot_for_enqueue(st=st, offset=1)
        v2 = await app._resolve_rotation_slot_for_enqueue(st=st, offset=2)
        v3 = await app._resolve_rotation_slot_for_enqueue(st=st, offset=3)
        assert v1[:2] == ("t0", "g0")
        assert v2[:2] == ("t3", "g3")
        assert v3[:2] == ("t0", "g0")  # wraps round-robin

    asyncio.run(_run())


def test_vibe_done_requires_selection(monkeypatch):
    ranked = ["t0:g0", "t1:g1"]
    team, app = _make_app(monkeypatch, ranked)
    from services.tg_bot_botapi.state_store import ChatState, STAGE_WAIT_VIBE

    async def _run():
        st = ChatState(chat_id=7, lyrics_text="x", bg_mode="footage")
        await app.store.set(st)
        await app._ask_vibe_shortlist(_Msg(), st)
        # Done with zero selected → alert, stays on WAIT_VIBE.
        cb = _CB("vibe:done")
        await app._handle_vibe_callback(cb)
        st = await app.store.get(7)
        assert st.stage == STAGE_WAIT_VIBE
        assert st.vibe_selected_ids == []

    asyncio.run(_run())


def test_vibe_auto_picks_top1(monkeypatch):
    ranked = ["t0:g0", "t1:g1", "t2:g2"]
    team, app = _make_app(monkeypatch, ranked)
    from services.tg_bot_botapi.state_store import ChatState, STAGE_WAIT_SUBTITLES_MODE

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
