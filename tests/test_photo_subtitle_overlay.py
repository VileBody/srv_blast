# -*- coding: utf-8 -*-
"""The photo subtitle overlay must stay isolated from the photos beneath it.

Regression 764d4a895a1e4e45b96c5dd046cb0a06 had Geometry2 adjustment layers
inside the nested ``Текст`` comp. Collapsing the subtitle chain let those effects
escape and transform every photo below the overlay.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def tpl() -> str:
    return (
        Path(__file__).resolve().parents[1] / "templates" / "photo_template.j2"
    ).read_text(encoding="utf-8")


def test_the_overlay_keeps_the_subtitle_comp_isolated(tpl: str) -> None:
    body = tpl[tpl.index("function addSubtitleOverlay"):tpl.index("function placePhoto")]
    assert "overlay.collapseTransformation = false;" in body
    assert "overlay.collapseTransformation = true;" not in body


def test_the_nested_subtitle_chain_is_never_collapsed(tpl: str) -> None:
    assert "function collapseNestedComps" not in tpl
    assert "collapseNestedComps(" not in tpl


def test_text_geometry_is_still_preserved(tpl: str) -> None:
    """Isolation must not become an excuse to stretch text horizontally."""
    assert 'tr.property("ADBE Scale").setValue([100, 100]);' in tpl


def test_direct_source_adjustments_are_disabled_before_nesting(tpl: str) -> None:
    """Top-level source adjustments are not part of the subtitle-only overlay."""
    disable_at = tpl.index("srcLayer.adjustmentLayer === true")
    nest_at = tpl.index("var overlay = comp.layers.add(subtitleComp);")
    assert disable_at < nest_at
    assert "srcLayer.enabled = false;" in tpl
