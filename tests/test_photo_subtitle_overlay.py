# -*- coding: utf-8 -*-
"""The subtitle overlay must not clip the widest text in the 4:3 render.

The subtitle comp is 1080 wide and the photo comp is 1920. A nested comp clips at
its OWN bounds, so anything wider than 1080 was cut off even though the frame had
room on both sides. Ordinary lines fit; the jakson focus word — the red one, set
about 2.5x the base size — did not.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def tpl() -> str:
    return (
        Path(__file__).resolve().parents[1] / "templates" / "photo_template.j2"
    ).read_text(encoding="utf-8")


def test_the_overlay_does_not_clip_at_the_subtitle_comp_bounds(tpl: str) -> None:
    assert "overlay.collapseTransformation = true;" in tpl


def test_collapse_is_applied_to_the_nested_layer_after_it_exists(tpl: str) -> None:
    assert tpl.index("var overlay = comp.layers.add(subtitleComp);") < tpl.index(
        "overlay.collapseTransformation = true;"
    )


def test_text_geometry_is_still_preserved(tpl: str) -> None:
    """Collapsing must not become an excuse to stretch the text horizontally:
    the point is to stop CLIPPING, not to rescale the subtitles."""
    assert 'tr.property("ADBE Scale").setValue([100, 100]);' in tpl


def test_adjustment_layers_inside_the_overlay_are_disabled_before_collapsing(tpl: str) -> None:
    """The one real hazard collapse introduces: an adjustment layer inside a
    COLLAPSED comp stops being contained by it and starts grading the parent
    comp's layers below — i.e. the photos. The template already disables them,
    and it must keep doing so BEFORE the layer is nested."""
    disable_at = tpl.index("srcLayer.adjustmentLayer === true")
    nest_at = tpl.index("var overlay = comp.layers.add(subtitleComp);")
    assert disable_at < nest_at
    assert "srcLayer.enabled = false;" in tpl
