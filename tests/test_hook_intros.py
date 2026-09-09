# -*- coding: utf-8 -*-
"""Shared hook intros registry (descriptions now, example videos later)."""
from __future__ import annotations

from core.hook_intros import HOOK_CATEGORY_ORDER, HOOK_INTROS, hook_intro


def test_all_categories_present_and_described():
    assert set(HOOK_CATEGORY_ORDER) == set(HOOK_INTROS)
    assert HOOK_CATEGORY_ORDER == ("sound", "object", "effect", "motion", "thought")
    for key in HOOK_CATEGORY_ORDER:
        entry = HOOK_INTROS[key]
        assert entry["text"].strip(), f"{key} has empty text"
        # videos start empty — get filled once clips are montaged
        assert entry["video"] == ""


def test_hook_intro_accessor():
    # «Прогрев» = звук ИЛИ видео в окне до дропа (F1/F6) — одна карточка.
    assert hook_intro("sound")["text"].startswith("🔥")
    assert hook_intro("unknown") is None
    assert hook_intro("") is None


def test_warmup_intro_describes_the_current_video_mix() -> None:
    text = hook_intro("sound")["text"]
    assert "на 10%" in text
    assert "за последнюю секунду" in text
    assert "без молнии" in text
