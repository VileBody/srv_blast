# -*- coding: utf-8 -*-
"""Parity: tg_bot_public mirrors the hook-intro helper + shared registry.

Both bots send hook descriptions from the shared core.hook_intros registry via
_send_hook_intro (text now, video file_id later). The category-picker UX is wired
in the test bot first; the public bot mirrors the helper for the upcoming port.
"""
from __future__ import annotations


def test_public_has_send_hook_intro():
    from services.tg_bot_public import app as pub

    assert hasattr(pub.BlastBotApp, "_send_hook_intro")


def test_shared_registry_importable_from_core():
    from core.hook_intros import HOOK_INTROS, hook_intro

    assert "thought" in HOOK_INTROS
    assert hook_intro("motion")["text"]
