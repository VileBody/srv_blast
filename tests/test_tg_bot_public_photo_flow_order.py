# -*- coding: utf-8 -*-
"""The photo flow is the footage flow with a different background.

Two separate defects were found here, both hidden by the same idea — that 4:3
needs its own everything:

1. The colour steps were skipped and `subtitle_color_hex` / `accent_color_hex`
   were wiped to "". Colours are a SUBTITLE feature: the photo build reuses the
   canonical subtitle project, so the same env applies. Nothing justified it.
2. The transition/stylization buttons were a bespoke 4:3 set (Hue/Saturation +
   Brightness/Contrast presets and a 4-frame intro) rather than the F3 library
   footage uses. Nothing else exercised them, and they had no preview reels —
   the previews were the visible symptom of the split.

Photo now takes the SAME effect steps as footage; the F3 block is rebuilt against
PHOTO_COMP at build time so those buttons drive the real effects. Hooks stay the
one skipped step. Asserted on both bots so public cannot drift back.
"""
from __future__ import annotations

import importlib
import inspect
import re
from typing import Callable

import pytest


BOTS = ("services.tg_bot_botapi.app", "services.tg_bot_public.app")


def _handler_src(bot: str, name: str) -> str:
    mod = importlib.import_module(bot)
    fn: Callable = getattr(mod.BlastBotApp, name)
    return inspect.getsource(fn)


def _calls(src: str) -> list[str]:
    """Ordered self._ask_* / self._proceed_* calls, minus the BACK branch (which
    intentionally points at the PREVIOUS step and would invert every check)."""
    out: list[str] = []
    skip_from: int | None = None
    for ln in src.splitlines():
        stripped = ln.strip()
        indent = len(ln) - len(ln.lstrip())
        if skip_from is not None:
            if stripped and indent <= skip_from:
                skip_from = None
            else:
                continue
        if re.match(r"if .*== BTN_BACK", stripped):
            skip_from = indent
            continue
        m = re.search(r"self\.(_ask_[a-z_0-9]+|_proceed_[a-z_0-9]+)\(", stripped)
        if m:
            out.append(m.group(1))
    return out


@pytest.mark.parametrize("bot", BOTS)
def test_photo_uses_the_shared_effect_steps_not_a_bespoke_pair(bot: str) -> None:
    """The bespoke 4:3 pickers must be unreachable — reachable-but-unused is the
    state that produced buttons nobody could vouch for."""
    src = inspect.getsource(importlib.import_module(bot))
    entries = re.findall(r"await self\._ask_photo_(?:style|transition)\(", src)
    # only the handlers' own BACK edges may still reference each other
    assert len(entries) <= 2, entries


def test_public_hub_routes_photo_through_the_footage_visuals_slot() -> None:
    """Both bg modes share one guard, so photo cannot silently lose the step."""
    src = _handler_src(BOTS[1], "_proceed_to_versions_or_confirm")
    assert re.search(r'bg_mode in \{"footage", "photo"\}[^\n]*\n[^\n]*visuals_done', src) or \
        re.search(r'bg_mode in \{"footage", "photo"\}', src), src
    calls = _calls(src)
    assert calls.index("_ask_visual_transition") < calls.index("_ask_subtitle_color")


def test_team_photo_enters_the_real_effect_pickers() -> None:
    src = _handler_src(BOTS[0], "_handle_wait_subtitles_mode")
    assert "_ask_effect_transition" in _calls(src)
    # ...and the hook sub-step is the one thing it skips
    assert "_ask_effect_hook" not in _calls(src)


def test_team_effect_chain_returns_photo_to_the_colours() -> None:
    calls = _calls(_handler_src(BOTS[0], "_effect_summary_and_continue"))
    assert "_ask_subtitle_color" in calls
    assert calls.index("_ask_subtitle_color") < calls.index("_ask_versions")


@pytest.mark.parametrize("bot", BOTS)
def test_the_photo_branch_never_wipes_the_chosen_colours(bot: str) -> None:
    src = inspect.getsource(importlib.import_module(bot))
    for hunk in re.findall(r'if st\.bg_mode == "photo":(?:\n(?:[ \t]{12,}.*)?)+', src):
        assert 'subtitle_color_hex = ""' not in hunk, hunk
        assert 'accent_color_hex = ""' not in hunk, hunk


@pytest.mark.parametrize("bot", BOTS)
def test_the_bespoke_4x3_grade_is_not_sent(bot: str) -> None:
    """Both are left unset so the template's own flash/grade cannot stack on top
    of the F3 effects now driving the same cuts."""
    src = inspect.getsource(importlib.import_module(bot))
    assert "photo_style=None," in src
    assert "photo_transition=None," in src


@pytest.mark.parametrize("bot", BOTS)
def test_photo_still_skips_hooks(bot: str) -> None:
    src = inspect.getsource(importlib.import_module(bot))
    assert re.search(r'if st\.bg_mode == "photo":\s*\n(?:.*\n)*?\s*st\.effect_hook = ""', src) or \
        re.search(r'if st\.bg_mode == "photo":\s*\n(?:.*\n)*?\s*st\.hook_enabled = False', src)
