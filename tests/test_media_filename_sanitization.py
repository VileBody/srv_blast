from __future__ import annotations

from pathlib import Path

from app.footage_comp import build_footage_layers


def _build_cfg(video_names: list[str]) -> dict:
    layers = []
    for idx, name in enumerate(video_names):
        layers.append(
            {
                "type": "footage",
                "name": f"v_{idx}",
                "file_name": name,
                "file_path": f"s3://bucket/{name}",
                "in_point": float(idx) * 1.0,
                "out_point": float(idx) * 1.0 + 1.0,
                "start_time": float(idx) * 1.0,
                "enabled": True,
                "src_w": 720,
                "src_h": 1280,
                "fit_mode": "cover",
            }
        )

    layers.append(
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
        }
    )
    return {"text_dur_hint": 10.0, "layers": layers}


def _video_file_names(layers: list[dict]) -> list[str]:
    out: list[str] = []
    for layer in layers:
        if str(layer.get("type")) != "footage":
            continue
        td = layer.get("text_data") if isinstance(layer.get("text_data"), dict) else {}
        meta = td.get("layer_meta") if isinstance(td.get("layer_meta"), dict) else {}
        if bool(meta.get("audioEnabled")) is True:
            continue
        src = td.get("source_footage") if isinstance(td.get("source_footage"), dict) else {}
        name = str(src.get("file_name") or "")
        if name:
            out.append(name)
    return out


def test_video_filename_is_windows_safe_normalized() -> None:
    cfg = _build_cfg(["1477812373564456_Imaginary PointsAlex Guevara Credits  alexguevara .mp4"])
    layers = build_footage_layers(
        repo_root=Path("."),
        footage_cfg=cfg,
        main_comp_name="Comp 1",
        text_comp_name="Text",
    )
    names = _video_file_names(layers)
    assert names == ["1477812373564456_Imaginary PointsAlex Guevara Credits alexguevara.mp4"]


def test_colliding_normalized_names_get_deterministic_suffix() -> None:
    cfg = _build_cfg(["clip  one .mp4", "clip one.mp4", "clip  one .mp4"])
    layers = build_footage_layers(
        repo_root=Path("."),
        footage_cfg=cfg,
        main_comp_name="Comp 1",
        text_comp_name="Text",
    )
    names = _video_file_names(layers)

    assert len(names) == 3
    assert len(set(names)) == 2
    assert sum(1 for n in names if "__" in n) == 1
