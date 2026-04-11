from __future__ import annotations

from render_v1.effects_logic import stack_to_ae_effects_conf


def test_stack_to_ae_effects_conf_skips_curves() -> None:
    effects_library = {
        "effectPresets": {
            "ef_curves": {
                "propertyTree": {"matchName": "ADBE CurvesCustom"},
                "exposedParams": [{"key": "master", "matchNamePath": "ADBE CurvesCustom-0001"}],
            },
            "ef_transform": {
                "propertyTree": {"matchName": "ADBE Geometry2"},
                "exposedParams": [{"key": "scale", "matchNamePath": "ADBE Geometry2-0003"}],
            },
        }
    }
    stack = [
        {"instanceId": "curves", "presetId": "ef_curves", "enabled": True, "overrides": {"master": {"keys": []}}},
        {"instanceId": "transform", "presetId": "ef_transform", "enabled": True, "overrides": {"scale": 110}},
    ]

    out = stack_to_ae_effects_conf(stack, effects_library, layer_in=0.0, layer_out=10.0)

    assert len(out) == 1
    assert out[0]["matchName"] == "ADBE Geometry2"
    assert "ADBE Geometry2-0003" in out[0]["params"]
