# -*- coding: utf-8 -*-
"""The photo flow is the footage flow with a different background.

Reported from the team bot: the photo branch ran its own truncated route — the
style/transition pair fired straight after the vibe, and then the flow jumped to
the version count with the subtitle/accent colour steps silently skipped and
`subtitle_color_hex` / `accent_color_hex` wiped. Colours are a SUBTITLE feature:
the photo build reuses the canonical subtitle project, so the same env
(SUBTITLES_FORCE_FILL_HEX / focus hex) applies — there was never a renderer
reason to drop them.

Order in both bots is now: subtitle mode → transition → stylization → colours,
with only hooks skipped (not ported to 4:3). Asserted on both so the public bot
cannot inherit the truncated version.
"""
from __future__ import annotations

import inspect
import re
from typing import Callable

import pytest


BOTS = ("services.tg_bot_botapi.app", "services.tg_bot_public.app")


def _handler_src(bot: str, name: str) -> str:
    import importlib

    mod = importlib.import_module(bot)
    fn: Callable = getattr(mod.BlastBotApp, name)
    return inspect.getsource(fn)


def _calls(src: str) -> list[str]:
    """Ordered self._ask_* / self._proceed_* calls, minus the BACK branch (which
    intentionally points at the PREVIOUS step and would invert every check)."""
    lines = src.splitlines()
    out: list[str] = []
    skip_until_dedent_from: int | None = None
    for ln in lines:
        stripped = ln.strip()
        indent = len(ln) - len(ln.lstrip())
        if skip_until_dedent_from is not None:
            if stripped and indent <= skip_until_dedent_from:
                skip_until_dedent_from = None
            else:
                continue
        if re.match(r"if .*== BTN_BACK", stripped):
            skip_until_dedent_from = indent
            continue
        m = re.search(r"self\.(_ask_[a-z_]+|_proceed_[a-z_]+)\(", stripped)
        if m:
            out.append(m.group(1))
    return out


@pytest.mark.parametrize("bot", BOTS)
def test_transition_comes_before_stylization(bot: str) -> None:
    """Mirrors the footage pair (шаг 1/2 переход → шаг 2/2 стилизация); the photo
    steps used to be numbered the other way round."""
    assert "_ask_photo_style" in _calls(_handler_src(bot, "_handle_wait_photo_transition"))
    assert "_ask_photo_transition" not in _calls(_handler_src(bot, "_handle_wait_photo_style"))


@pytest.mark.parametrize("bot", BOTS)
def test_stylization_is_followed_by_the_colour_steps(bot: str) -> None:
    calls = _calls(_handler_src(bot, "_handle_wait_photo_style"))
    # team asks the colours directly; public routes through its post-settings hub,
    # which owns the colour slot for every bg mode.
    assert calls and calls[-1] in {"_ask_subtitle_color", "_proceed_to_versions_or_confirm"}


@pytest.mark.parametrize("bot", BOTS)
def test_the_photo_branch_never_wipes_the_chosen_colours(bot: str) -> None:
    """The regression that removed the steps: whatever the user picked was reset
    to "" on the photo path, so even a colour chosen earlier never shipped."""
    import importlib

    src = inspect.getsource(importlib.import_module(bot))
    for hunk in re.findall(r'if st\.bg_mode == "photo":(?:\n(?:[ \t]{12,}.*)?)+', src):
        assert 'subtitle_color_hex = ""' not in hunk, hunk
        assert 'accent_color_hex = ""' not in hunk, hunk


@pytest.mark.parametrize("bot", BOTS)
def test_photo_still_skips_hooks(bot: str) -> None:
    """Hooks are the ONE documented gap for 4:3 — the flow must not offer them."""
    import importlib

    src = inspect.getsource(importlib.import_module(bot))
    assert re.search(r'if st\.bg_mode == "photo":\s*\n(?:.*\n)*?\s*st\.hook_enabled = False', src)


def test_public_hub_asks_photo_visuals_before_the_colours() -> None:
    """In the public bot both slots live in one hub, so their ORDER is decided by
    the order of the guards inside it — asserting the two handlers separately
    would still pass if the colour guard were moved above the visuals one."""
    src = _handler_src(BOTS[1], "_proceed_to_versions_or_confirm")
    calls = _calls(src)
    assert "_ask_photo_transition" in calls, calls
    assert "_ask_subtitle_color" in calls, calls
    assert calls.index("_ask_photo_transition") < calls.index("_ask_subtitle_color")
    # and photo sits alongside footage in that slot, not on a bypass around it
    assert calls.index("_ask_visual_transition") < calls.index("_ask_subtitle_color")


def test_team_photo_reaches_versions_without_the_hook_step() -> None:
    calls = _calls(_handler_src(BOTS[0], "_handle_wait_accent_color"))
    assert "_ask_versions" in calls
    assert calls.index("_ask_versions") < calls.index("_ask_hook_choice")
