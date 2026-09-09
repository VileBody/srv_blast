"""Output geometries the render stack can emit.

The vertical 1080x1920 reel was the only shape for a long time, so its numbers
sat hardcoded in two unrelated places: the AE comp spec (`project_config`) and
the footage cover transform (`step3_template` -> `footage_comp`). They have to
agree — a mismatch silently mis-scales every clip — so with more than one shape
in play they get ONE source, here.

What deliberately does NOT vary per preset is the SUBTITLE stack. The text comp
stays 1080x1920 in every geometry and is placed into the main comp as a nested
layer, exactly the way the 4:3 photo render already does it. That is what keeps
`text_flow_renderer` (and its hardcoded 1080x1920 coordinates) untouched: a new
output shape re-frames the subtitles, it does not re-author them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List

# The authored subtitle geometry. Nested, never rebuilt — see module docstring.
TEXT_COMP_W = 1080
TEXT_COMP_H = 1920


@dataclass(frozen=True)
class RenderPreset:
    name: str
    width: int
    height: int
    label_ru: str

    @property
    def collapse_text_precomp(self) -> bool:
        """Always False. Collapsing this precomp is not survivable.

        Collapse Transformations was tempting here: a nested comp CLIPS at its own
        bounds, so inside a frame wider than the 1080-wide text comp the widest
        text (the jakson focus word runs to ~2.5x the base size) is cut even
        though the frame has room. Collapsing removes that intermediate raster.

        But it also dissolves the precomp's ISOLATION. Adjustment layers inside a
        collapsed comp stop being confined to it and apply to everything beneath
        them in the parent — so the jakson text animators started squeezing the
        FOOTAGE into the text comp's 1080x1080 bounds the moment a subtitle
        appeared. Observed on the first 16:9 render; vertical never collapsed and
        never showed it.

        Clipping at 1080 is the authored behaviour anyway: in the vertical frame
        the comp is exactly as wide as the picture, so a word that overflows is
        already cut there. Keeping that identical in a wider frame costs nothing
        that production has ever had, while collapsing costs the footage.

        (The 4:3 photo render does collapse, and can: it composites stills with no
        adjustment layers under the subtitles.)
        """
        return False

    @property
    def text_precomp_position(self) -> List[float]:
        return [self.width / 2.0, self.height / 2.0, 0.0]


VERTICAL = RenderPreset("vertical", 1080, 1920, "Вертикальный 9:16")
WIDE = RenderPreset("wide", 1920, 1080, "Горизонтальный 16:9")
SQUARE = RenderPreset("square", 1080, 1080, "Квадрат 1:1")

RENDER_PRESETS: Dict[str, RenderPreset] = {p.name: p for p in (VERTICAL, WIDE, SQUARE)}

DEFAULT_PRESET_NAME = VERTICAL.name

# Env carrying the choice into the build subprocess. Absent => vertical, so every
# existing job renders exactly as before.
RENDER_PRESET_ENV = "RENDER_PRESET"


def get_preset(name: str | None) -> RenderPreset:
    key = str(name or "").strip().lower() or DEFAULT_PRESET_NAME
    preset = RENDER_PRESETS.get(key)
    if preset is None:
        raise RuntimeError(
            f"unknown render preset {name!r} (expected one of {sorted(RENDER_PRESETS)})"
        )
    return preset


def active_preset() -> RenderPreset:
    return get_preset(os.environ.get(RENDER_PRESET_ENV))


def text_precomp_placement(preset: RenderPreset) -> Dict[str, object]:
    """Transform that centres the 1080x1920 subtitle comp in this geometry.

    The anchor stays the TEXT comp's own centre and the scale stays 100: the
    subtitles keep their authored pixel size, they are only re-framed. Scaling
    them to "fit" a wider frame would shrink the type instead.
    """
    return {
        "anchor": [TEXT_COMP_W / 2.0, TEXT_COMP_H / 2.0, 0.0],
        "position": preset.text_precomp_position,
        "scale": [100, 100, 100],
        "rotationZ": 0,
        "opacity": 100,
        "collapseTransformation": preset.collapse_text_precomp,
    }
