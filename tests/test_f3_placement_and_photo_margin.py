# -*- coding: utf-8 -*-
"""Two defects behind "wave and minimax ruin the video" on the 4:3 render.

1) PLACEMENT. Two keys are in circulation. The transitions parse CONFIG.place;
   the stylizations (blackwhite / crystal_glow / night_vision / wave) read
   CONFIG.placeRef and default it to "Текст". Only `place` was ever sent, so the
   #182 fix moved the transitions and left those four looking for a layer that
   does not exist in the photo comp — they found nothing and silently skipped
   their move.

2) MARGIN. The photo cover overscan was 1.002 — about 2px per side at 1920. Every
   effect in the set displaces more than that: Wave Warp ~6px, Scatter ~23px. The
   photo edge got dragged inward and the comp background showed through. Footage
   has carried 1.0167 since it was written, with the comment "small overscan to
   prevent black edges", and the effects were authored against that margin.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from mlcore.hooks.f3_effect import overlay as f3


ROOT = Path(__file__).resolve().parents[1]
STYLIZATIONS_READING_PLACE_REF = (
    "extra/rebuild_blackwhite.jsx",
    "extra/rebuild_crystal_glow.jsx",
    "extra/rebuild_night_vision.jsx",
    "extra/rebuild_wave.jsx",
)


def _script(rel: str) -> str:
    return io.open(ROOT / "mlcore" / "hooks" / "f3_effect" / rel, encoding="utf-8").read()


@pytest.mark.parametrize("rel", STYLIZATIONS_READING_PLACE_REF)
def test_these_scripts_really_do_read_place_ref(rel: str) -> None:
    """Pins the premise. If one is ever rewritten to use CONFIG.place, this test
    should be revisited rather than the payload silently carrying a dead key."""
    src = _script(rel)
    assert "findLayer(comp,CONFIG.placeRef)" in src, rel


def test_the_payload_carries_both_placement_keys() -> None:
    photo = f3.build_overlay_jsx(
        transition="minimax", extra="wave", drop_time=8.0, comp_var="PHOTO_COMP"
    )
    assert 'var __f3_place_ref = "SUBTITLES_OVERLAY";' in photo
    assert "placeRef: __f3_place_ref" in photo
    # every script invocation must get it, not just the first
    payloads = re.findall(r"\$\.global\.__BLAST = \{[^\n]*\};", photo)
    assert payloads
    for p in payloads:
        if "targetCompName" in p and "cuts:" in p:
            assert "placeRef" in p, p


def test_footage_placement_is_unchanged() -> None:
    """The value sent for footage equals the scripts' own default, so passing it
    explicitly cannot move anything there."""
    footage = f3.build_overlay_jsx(transition="minimax", extra="wave", drop_time=8.0)
    assert f'var __f3_place_ref = "{f3._PLACE_REF}";' in footage
    for rel in STYLIZATIONS_READING_PLACE_REF:
        assert f'placeRef:"{f3._PLACE_REF}"' in _script(rel).replace(" ", ""), rel


def test_photo_cover_margin_survives_the_effect_displacements() -> None:
    from app.photo_comp import PHOTO_ANIM, PHOTO_COMP_H, PHOTO_COMP_W

    overscan = float(PHOTO_ANIM["overscan"])
    margin_x = (overscan - 1.0) / 2.0 * PHOTO_COMP_W
    margin_y = (overscan - 1.0) / 2.0 * PHOTO_COMP_H

    # Scatter is the largest continuous displacement in the set: 20px authored,
    # carried to 4:3 by the isotropic factor (~1.14).
    largest_displacement_px = 20 * 1.15
    assert margin_x > largest_displacement_px, margin_x
    assert margin_y > largest_displacement_px, margin_y


def test_photo_margin_is_at_least_the_footage_one() -> None:
    """Footage's 1.0167 exists to stop black edges. Photo cannot be tighter than
    the format whose effects it borrows."""
    from app.photo_comp import PHOTO_ANIM

    src = (ROOT / "app" / "footage_comp.py").read_text(encoding="utf-8")
    m = re.search(r"OVERSCAN: float = ([0-9.]+)", src)
    assert m, "footage overscan constant moved — update this test"
    assert float(PHOTO_ANIM["overscan"]) >= float(m.group(1))
