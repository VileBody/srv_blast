from __future__ import annotations

from pathlib import Path

from app.footage_comp import build_footage_layers


def _cfg() -> dict:
    return {
        "text_dur_hint": 10.0,
        "layers": [
            {
                "type": "audio_only",
                "name": "audio",
                "file_name": "audio_source.mp3",
                "file_path": "s3://bucket/audio_source.mp3",
                "in_point": 0.0,
                "out_point": 10.0,
                "start_time": 0.0,
                "enabled": True,
                "audio_enabled": True,
                "video_enabled": False,
                "target_comp": "Comp 1",
            }
        ],
    }


def _audio_layer(layers: list[dict]) -> dict:
    for it in layers:
        td = it.get("text_data") if isinstance(it.get("text_data"), dict) else {}
        meta = td.get("layer_meta") if isinstance(td.get("layer_meta"), dict) else {}
        if str(it.get("type")) == "footage" and bool(meta.get("audioEnabled")) is True:
            return it
    raise AssertionError("audio layer not found")


def test_audio_envelope_defaults(monkeypatch) -> None:
    monkeypatch.delenv("AUDIO_FADE_IN_S", raising=False)
    monkeypatch.delenv("AUDIO_FADE_OUT_S", raising=False)
    monkeypatch.delenv("AUDIO_FADE_MIN_DB", raising=False)

    layers = build_footage_layers(
        repo_root=Path("."),
        footage_cfg=_cfg(),
        main_comp_name="Comp 1",
        text_comp_name="Text",
    )
    env = _audio_layer(layers)["text_data"]["audio_envelope"]
    assert abs(float(env["fade_in_s"]) - 0.5) <= 1e-6
    assert abs(float(env["fade_out_s"]) - 0.5) <= 1e-6
    assert abs(float(env["min_db"]) - (-48.0)) <= 1e-6


def test_audio_envelope_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("AUDIO_FADE_IN_S", "0.25")
    monkeypatch.setenv("AUDIO_FADE_OUT_S", "0.75")
    monkeypatch.setenv("AUDIO_FADE_MIN_DB", "-36")

    layers = build_footage_layers(
        repo_root=Path("."),
        footage_cfg=_cfg(),
        main_comp_name="Comp 1",
        text_comp_name="Text",
    )
    env = _audio_layer(layers)["text_data"]["audio_envelope"]
    assert abs(float(env["fade_in_s"]) - 0.25) <= 1e-6
    assert abs(float(env["fade_out_s"]) - 0.75) <= 1e-6
    assert abs(float(env["min_db"]) - (-36.0)) <= 1e-6
