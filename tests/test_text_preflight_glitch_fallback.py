from __future__ import annotations

import pytest

from core.fps import COMP_FPS
from app.text_comp import _preflight_clamp_text_layers


MINE_COMP_NAME = 'Текст "Mine"'


def _mine_styles(n: int) -> list[dict]:
    return [{"i": i, "font": "Point-ExtraBold", "fontSize": 100} for i in range(n)]


def test_preflight_glitch_peak_zero_window_merges_into_mine() -> None:
    layers = [
        {
            "type": "text",
            "name": "glitch_peak_prefix",
            "text": "HIS EYES WERE LIKE",
            "in_point": 8.51,
            "out_point": 8.51,
            "text_data": {
                "layer_meta": {"comp_name_target": "Текст"},
                "char_styles_ungrouped": [{"i": 0, "font": "Point-SemiBold", "fontSize": 100}],
            },
            "props": {},
            "effects": {},
        },
        {
            "type": "text",
            "name": "mine",
            "text": "MINE",
            "in_point": 8.51,
            "out_point": 9.0,
            "text_data": {
                "layer_meta": {"comp_name_target": MINE_COMP_NAME},
                "char_styles_ungrouped": _mine_styles(4),
            },
            "props": {},
            "effects": {},
        },
    ]

    _preflight_clamp_text_layers(
        layers,
        fps=COMP_FPS,
        strict=True,
        mine_comp_name=MINE_COMP_NAME,
    )

    glitch = layers[0]
    mine = layers[1]

    assert glitch["text"] == ""
    assert glitch["text_data"]["layer_meta"]["enabled"] is False
    assert glitch["text_data"]["char_styles_ungrouped"] == []

    assert mine["text"] == "HIS EYES WERE LIKE\rMINE"
    assert len(mine["text_data"]["char_styles_ungrouped"]) == len("HIS EYES WERE LIKE\rMINE")


def test_preflight_keeps_strict_failure_for_non_glitch_out_le_in() -> None:
    layers = [
        {
            "type": "text",
            "name": "regular_layer",
            "text": "X",
            "in_point": 1.0,
            "out_point": 1.0,
            "text_data": {"layer_meta": {"comp_name_target": "Текст"}},
            "props": {},
            "effects": {},
        }
    ]

    with pytest.raises(ValueError, match="out<=in"):
        _preflight_clamp_text_layers(
            layers,
            fps=COMP_FPS,
            strict=True,
            mine_comp_name=MINE_COMP_NAME,
        )


def test_preflight_allows_zero_duration_adjustment_in_strict_mode() -> None:
    layers = [
        {
            "type": "adjustment",
            "name": "Adjustment Layer 3",
            "in_point": 15.59,
            "out_point": 15.59,
            "text_data": {"layer_meta": {"comp_name_target": "Текст"}},
            "props": {},
            "effects": {},
        }
    ]

    _preflight_clamp_text_layers(
        layers,
        fps=COMP_FPS,
        strict=True,
        mine_comp_name=MINE_COMP_NAME,
    )

    assert float(layers[0]["in_point"]) == pytest.approx(15.59)
    assert float(layers[0]["out_point"]) == pytest.approx(15.59)
