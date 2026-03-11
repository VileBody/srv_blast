from __future__ import annotations

import pytest

from app.text_comp import _apply_text_time_shift, _resolve_text_layer_shift_s


def test_text_time_shift_moves_in_out_and_keyframes() -> None:
    layers = [
        {
            "type": "text",
            "name": "L1",
            "in_point": 1.0,
            "out_point": 2.0,
            "props": {
                "tf_opacity": {
                    "match_name": "ADBE Opacity",
                    "value": None,
                    "keyframes": [{"t": 1.1, "v": 0}, {"t": 1.7, "v": 100}],
                }
            },
        },
        {
            "type": "footage",
            "name": "V1",
            "in_point": 5.0,
            "out_point": 6.0,
        },
    ]

    _apply_text_time_shift(layers, shift_s=0.3)

    assert abs(float(layers[0]["in_point"]) - 0.7) <= 1e-6
    assert abs(float(layers[0]["out_point"]) - 1.7) <= 1e-6
    kfs = layers[0]["props"]["tf_opacity"]["keyframes"]
    assert abs(float(kfs[0]["t"]) - 0.8) <= 1e-6
    assert abs(float(kfs[1]["t"]) - 1.4) <= 1e-6

    # Non-text layers must not be shifted.
    assert abs(float(layers[1]["in_point"]) - 5.0) <= 1e-6
    assert abs(float(layers[1]["out_point"]) - 6.0) <= 1e-6


def test_text_time_shift_allows_negative_in_point() -> None:
    layers = [
        {
            "type": "text",
            "name": "L2",
            "in_point": 0.1,
            "out_point": 0.9,
            "props": {},
        }
    ]
    _apply_text_time_shift(layers, shift_s=0.3)
    assert abs(float(layers[0]["in_point"]) - (-0.2)) <= 1e-6
    assert abs(float(layers[0]["out_point"]) - 0.6) <= 1e-6


def test_resolve_text_shift_classic_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEXT_SUBTITLE_PRESET", raising=False)
    monkeypatch.delenv("TEXT_LAYER_TIME_SHIFT_S", raising=False)
    assert abs(_resolve_text_layer_shift_s() - 0.3) <= 1e-9


def test_resolve_text_shift_impulse_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT_SUBTITLE_PRESET", "impulse")
    monkeypatch.setenv("TEXT_LAYER_TIME_SHIFT_S", "0.3")
    assert abs(_resolve_text_layer_shift_s() - 0.0) <= 1e-9


def test_resolve_text_shift_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT_SUBTITLE_PRESET", "legacy")
    with pytest.raises(RuntimeError, match="Invalid TEXT_SUBTITLE_PRESET"):
        _resolve_text_layer_shift_s()
