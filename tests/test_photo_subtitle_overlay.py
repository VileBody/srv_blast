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


def test_the_whole_nesting_chain_is_collapsed_not_just_the_outer_layer(tpl: str) -> None:
    """Collapsing only the overlay moved the clip one level down: the subtitles
    live in a "Текст" precomp, also 1080 wide, INSIDE the subtitle comp. The
    widest text was still cut at 1080."""
    assert "function collapseNestedComps(sourceComp, depth)" in tpl
    assert "collapseNestedComps(subtitleComp, 0);" in tpl
    # recursive — a precomp can hold further precomps
    assert "collapseNestedComps(src, depth + 1);" in tpl
    assert "depth > 3" in tpl  # and bounded, so a cycle cannot hang the build


def test_collapse_is_skipped_where_it_would_change_the_look(tpl: str) -> None:
    """Collapse drops a layer's effects and replaces its blending mode. Losing a
    deliberate look is worse than trimming a wide word, so those layers keep
    their raster."""
    assert 'L.property("ADBE Effect Parade")' in tpl
    assert "L.blendingMode === BlendingMode.NORMAL" in tpl
    assert "if (!hasFx && plainBlend)" in tpl
    # AE refuses collapse on some layer types; asking first avoids a throw
    assert "L.canSetCollapseTransformation" in tpl


def test_adjustment_layers_are_never_collapsed(tpl: str) -> None:
    """The opposite hazard: a COLLAPSED adjustment layer escapes its comp and
    starts grading the parent's layers below — here, the photos."""
    body = tpl[tpl.index("function collapseNestedComps"):tpl.index("function addSubtitleOverlay")]
    assert "if (L.adjustmentLayer === true) continue;" in body


def test_adjustment_layers_inside_the_overlay_are_disabled_before_collapsing(tpl: str) -> None:
    """The one real hazard collapse introduces: an adjustment layer inside a
    COLLAPSED comp stops being contained by it and starts grading the parent
    comp's layers below — i.e. the photos. The template already disables them,
    and it must keep doing so BEFORE the layer is nested."""
    disable_at = tpl.index("srcLayer.adjustmentLayer === true")
    nest_at = tpl.index("var overlay = comp.layers.add(subtitleComp);")
    assert disable_at < nest_at
    assert "srcLayer.enabled = false;" in tpl
