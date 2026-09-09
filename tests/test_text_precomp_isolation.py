# -*- coding: utf-8 -*-
"""The subtitle precomp must stay isolated from the footage beneath it.

Regression from the first 16:9 render: with the jakson subtitle style the picture
began squeezing into a 1080x1080 square the moment text appeared.

Cause: the wide preset switched Collapse Transformations on for the nested
1080-wide text comp, to stop wide text being clipped at its bounds. Collapsing
also dissolves the comp's isolation — adjustment layers inside it stop being
confined and apply to everything beneath them in the parent, so the jakson text
animators started transforming the FOOTAGE.

Clipping at 1080 is the authored behaviour anyway: in the vertical frame the comp
is exactly as wide as the picture, so an overflowing word is already cut there.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.footage_comp import build_footage_layers
from app.render_presets import get_preset, text_precomp_placement

PRESETS = ("vertical", "wide", "square")


def _footage_cfg(comp_w: int, comp_h: int) -> Dict[str, Any]:
    return {
        "main_comp_w": comp_w,
        "main_comp_h": comp_h,
        "text_dur_hint": 6.0,
        "layers": [
            {
                "type": "footage",
                "name": "clip.mp4",
                "file_name": "clip.mp4",
                "file_path": "s3://b/clip.mp4",
                "src_w": 1920,
                "src_h": 1080,
                "in_point": 0.0,
                "out_point": 3.0,
                "start_time": 0.0,
                "duration_sec": 3.0,
            }
        ],
    }


def _precomp_layer(preset_name: str) -> Dict[str, Any]:
    preset = get_preset(preset_name)
    cfg = _footage_cfg(preset.width, preset.height)
    layers: List[Any] = build_footage_layers(
        footage_cfg=cfg,
        main_comp_name="Comp 1",
        text_comp_name="Текст",
        composition_dur=6.0,
        precomp_z_index=9999,
        repo_root=Path.cwd(),
        precomp_placement=text_precomp_placement(preset),
    )
    pre = [l for l in layers if l.get("type") == "precomp"]
    assert pre, f"no text precomp layer built for {preset_name}"
    return pre[0]["text_data"]["layer_meta"]


@pytest.mark.parametrize("preset", PRESETS)
def test_the_text_precomp_never_collapses(preset: str) -> None:
    # This is the single flag that decides whether adjustment layers stay put.
    assert _precomp_layer(preset).get("collapseTransformation") is False


@pytest.mark.parametrize("preset", PRESETS)
def test_the_precomp_is_centred_without_being_rescaled(preset: str) -> None:
    # Re-framed, not resized: scaling to "fit" a wider frame would shrink the type.
    p = get_preset(preset)
    placement = text_precomp_placement(p)
    assert placement["scale"] == [100, 100, 100]
    assert placement["position"] == [p.width / 2.0, p.height / 2.0, 0.0]


def test_the_footage_layer_is_not_touched_by_the_placement() -> None:
    # The footage keeps its own cover transform whatever the text comp does.
    preset = get_preset("wide")
    cfg = _footage_cfg(preset.width, preset.height)
    layers = build_footage_layers(
        footage_cfg=cfg,
        main_comp_name="Comp 1",
        text_comp_name="Текст",
        composition_dur=6.0,
        precomp_z_index=9999,
        repo_root=Path.cwd(),
        precomp_placement=text_precomp_placement(preset),
    )
    footage = [l for l in layers if l.get("type") == "footage"]
    assert footage
    scale = footage[0]["props"]["tf_scale"]["value"]
    # 1920x1080 into 1920x1080 is 1:1 (plus the fixed overscan) and uniform —
    # a non-uniform pair here would mean the picture is being distorted.
    assert scale[0] == pytest.approx(scale[1])
    assert scale[0] == pytest.approx(101.67, abs=0.1)
