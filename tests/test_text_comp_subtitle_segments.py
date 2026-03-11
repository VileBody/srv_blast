from __future__ import annotations

from app.text_comp import build_text_layers


def test_build_text_layers_from_subtitle_segments_preserves_tags_and_exit(monkeypatch) -> None:
    monkeypatch.setenv("TEXT_SUBTITLE_PRESET", "impulse")

    full_edit_config = {
        "composition": {"fps": 23.976, "dur": 4.0},
        "subtitle_segments": [
            {"text": "мы станем", "tag": "long", "in_point": 0.0, "out_point": 1.9},
            {"text": "чужими", "tag": "short", "in_point": 1.9, "out_point": 2.39},
            {"text": "дальше", "tag": "long", "in_point": 2.39, "out_point": 4.0},
        ],
    }

    layers = build_text_layers(
        full_edit_config=full_edit_config,
        text_comp_name="Текст",
        mine_comp_name="Mine",
    )
    assert len(layers) == 3

    m0 = layers[0]["text_data"]["layer_meta"]
    m1 = layers[1]["text_data"]["layer_meta"]
    m2 = layers[2]["text_data"]["layer_meta"]

    assert m0["subtitle_tag"] == "long"
    assert m1["subtitle_tag"] == "short"
    assert m2["subtitle_tag"] == "long"
    assert abs(float(m0["impulse_exit_t"]) - 1.9) < 1e-9
    assert "impulse_exit_t" not in m1
    assert "impulse_exit_t" not in m2

