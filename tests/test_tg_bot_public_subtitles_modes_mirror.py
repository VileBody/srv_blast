# -*- coding: utf-8 -*-
"""Parity: tg_bot_public mirrors the 5th-template subtitle modes (trendy/brat).

The user-facing picker stays curated in the public bot (these are validated in
the test bot first), but the mode constants + button→mode mapping are mirrored
so the data layer matches tg_bot_botapi and the modes resolve identically.
"""
from __future__ import annotations


def test_public_maps_trendy_and_brat_modes():
    from services.tg_bot_public import app as pub
    from core.subtitles_mode import (
        SUBTITLES_MODE_BRAT_5TH,
        SUBTITLES_MODE_TRENDY_5TH,
    )

    modes = set(pub._SUBTITLES_MODE_BY_BUTTON.values())
    assert SUBTITLES_MODE_TRENDY_5TH in modes
    assert SUBTITLES_MODE_BRAT_5TH in modes


def test_public_picker_includes_5th_modes():
    # trendy/brat are now surfaced in the public subtitle picker.
    from services.tg_bot_public import app as pub

    assert pub.BTN_SUB_MODE_TRENDY in pub.SUBTITLES_MODE_BUTTONS
    assert pub.BTN_SUB_MODE_BRAT in pub.SUBTITLES_MODE_BUTTONS


def test_5th_modes_normalize_in_core():
    from core.subtitles_mode import (
        SUBTITLES_MODE_BRAT_5TH,
        SUBTITLES_MODE_TRENDY_5TH,
        SUBTITLES_MODE_VALUES,
        normalize_subtitles_mode,
    )

    assert SUBTITLES_MODE_TRENDY_5TH in SUBTITLES_MODE_VALUES
    assert SUBTITLES_MODE_BRAT_5TH in SUBTITLES_MODE_VALUES
    assert normalize_subtitles_mode("trendy_5th") == SUBTITLES_MODE_TRENDY_5TH
    assert normalize_subtitles_mode("brat_5th") == SUBTITLES_MODE_BRAT_5TH
