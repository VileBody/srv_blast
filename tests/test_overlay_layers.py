from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.footage_comp import build_footage_layers
import mlcore.gemini_postprocess as gp
from mlcore.gemini_postprocess import render_all_steps
from mlcore.models.full_plan import FullPlanPayload


def _tok(text: str, t_start: float, t_end: float, trailing: str) -> dict:
    return {"text": text, "t_start": t_start, "t_end": t_end, "trailing": trailing}


def _plan(subs_start: float, subs_end: float) -> FullPlanPayload:
    subtitles = {
        "clip": {"start": subs_start, "end": subs_end},
        "block_1": {"phrase": "b1", "tokens": [_tok("a", subs_start, subs_start + 1.0, "")]},
        "block_2": {
            "p1": {"phrase": "b2p1", "tokens": [_tok("b", subs_start + 1.0, subs_start + 2.0, "")]},
            "p2": {"phrase": "b2p2", "tokens": [_tok("c", subs_start + 2.0, subs_start + 3.0, "")]},
        },
        "block_3": {"phrase": "b3", "tokens": [_tok("d", subs_start + 3.0, subs_start + 4.0, "")]},
        "block_4": {
            "p1": {"phrase": "b4p1", "tokens": [_tok("e", subs_start + 4.0, subs_start + 5.0, "")]},
            "p2": {"phrase": "b4p2", "tokens": [_tok("f", subs_start + 5.0, subs_start + 6.0, "")]},
        },
        "block_5": {
            "slowly_in": {"phrase": "s", "tokens": [_tok("g", subs_start + 6.0, subs_start + 7.0, "")]},
            "fast_reveal": {"phrase": "f", "tokens": [_tok("h", subs_start + 7.0, subs_start + 8.0, "")]},
            "glitch_peak": {"phrase": "g", "tokens": [_tok("i", subs_start + 8.0, subs_start + 9.0, "")]},
            "mine": {"phrase": "j", "tokens": [_tok("j", subs_start + 9.0, subs_start + 9.5, "")]},
        },
        "block_6": {"phrase": "b6", "tokens": [_tok("k", subs_start + 9.5, subs_start + 11.0, "")]},
        "block_7": {
            "part1": {"phrase": "p1", "tokens": [_tok("l", subs_start + 11.0, subs_start + 12.0, "")]},
            "part2": {"phrase": "p2", "tokens": [_tok("m", subs_start + 12.0, subs_start + 14.0, "")]},
        },
    }
    footage = {
        "clips": [
            {
                "file_name": "clip1.mp4",
                "fit_mode": "cover",
                "in_point": subs_start,
                "out_point": subs_end,
                "start_time": subs_start,
            }
        ],
        "allow_gaps": False,
    }
    return FullPlanPayload.model_validate(
        {
            "audio": {"clip_start_abs": subs_start, "clip_end_abs": subs_end, "moment_of_interest_sec": 0.0},
            "subtitles": subtitles,
            "footage": footage,
        }
    )


def test_stage3_overlay_enabled_by_style_tiling(monkeypatch, tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    dummy_audio = tmp_path / "audio.mp3"
    dummy_audio.write_bytes(b"fake")

    inv = {
        "assets": [
            {
                "file_name": "clip1.mp4",
                "file_path": "s3://bucket/pinterest_collection/Rock/dark_forest/clip1.mp4",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 10.0,
            }
        ]
    }
    inv_path = tmp_path / "inv.json"
    inv_path.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")

    overlay_inv = {
        "assets": [
            {
                "file_name": "ov1.mp4",
                "file_path": "s3://bucket/pinterest_collection/Rock/dark_forest/overlays/ov1.mp4",
                "src_w": 1080,
                "src_h": 1920,
                "duration_sec": 3.0,
            }
        ]
    }
    overlay_inv_path = tmp_path / "overlay_inv.json"
    overlay_inv_path.write_text(json.dumps(overlay_inv, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("AUDIO_FILE_PATH", str(dummy_audio))
    monkeypatch.setenv("AUDIO_DIR", str(dummy_audio.parent))
    monkeypatch.setenv("AUDIO_FILE_NAME", "audio_source.mp3")
    monkeypatch.setenv("OVERLAY_ENABLED", "1")
    monkeypatch.setenv("OVERLAY_INVENTORY_JSON", str(overlay_inv_path))
    monkeypatch.setenv("OVERLAY_MATCH_MODE", "by_style")
    monkeypatch.setenv("OVERLAY_SELECTION_SEED", "seed1")

    out_dir = tmp_path / "out"
    data_dir = tmp_path / "data"
    render_all_steps(
        repo_root=repo_root,
        plan=_plan(100.0, 115.0),
        footage_inventory_json=inv_path,
        out_dir=out_dir,
        data_dir=data_dir,
    )

    footage_cfg = json.loads((out_dir / "footage_config.json").read_text(encoding="utf-8"))
    overlays = [x for x in footage_cfg.get("layers", []) if isinstance(x, dict) and x.get("type") == "overlay"]
    assert len(overlays) == 5
    assert overlays[0]["in_point"] == 0.0
    assert overlays[0]["out_point"] == 3.0
    assert overlays[-1]["out_point"] == 15.0
    for i in range(len(overlays) - 1):
        assert abs(float(overlays[i]["out_point"]) - float(overlays[i + 1]["in_point"])) <= 1e-6


def test_stage3_overlay_enabled_requires_inventory_env(monkeypatch, tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    dummy_audio = tmp_path / "audio.mp3"
    dummy_audio.write_bytes(b"fake")

    inv = {
        "assets": [
            {
                "file_name": "clip1.mp4",
                "file_path": "s3://bucket/pinterest_collection/Rock/dark_forest/clip1.mp4",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 10.0,
            }
        ]
    }
    inv_path = tmp_path / "inv.json"
    inv_path.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("AUDIO_FILE_PATH", str(dummy_audio))
    monkeypatch.setenv("AUDIO_DIR", str(dummy_audio.parent))
    monkeypatch.setenv("AUDIO_FILE_NAME", "audio_source.mp3")
    monkeypatch.setenv("OVERLAY_ENABLED", "1")
    monkeypatch.setenv("OVERLAY_SOURCE_MODE", "inventory")
    monkeypatch.delenv("OVERLAY_INVENTORY_JSON", raising=False)

    with pytest.raises(RuntimeError, match="OVERLAY_INVENTORY_JSON"):
        render_all_steps(
            repo_root=repo_root,
            plan=_plan(100.0, 115.0),
            footage_inventory_json=inv_path,
            out_dir=tmp_path / "out",
            data_dir=tmp_path / "data",
        )


def test_stage3_overlay_enabled_s3_prefix_global(monkeypatch, tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    dummy_audio = tmp_path / "audio.mp3"
    dummy_audio.write_bytes(b"fake")

    inv = {
        "assets": [
            {
                "file_name": "clip1.mp4",
                "file_path": "s3://bucket/pinterest_collection/Rock/dark_forest/clip1.mp4",
                "src_w": 720,
                "src_h": 1280,
                "duration_sec": 10.0,
            }
        ]
    }
    inv_path = tmp_path / "inv.json"
    inv_path.write_text(json.dumps(inv, ensure_ascii=False), encoding="utf-8")

    class _FakeS3:
        def list_objects_v2(self, **kwargs):
            del kwargs
            return {
                "IsTruncated": False,
                "Contents": [
                    {"Key": "overlays/ovA.mp4"},
                    {"Key": "overlays/ovB.mp4"},
                ],
            }

    monkeypatch.setattr(gp, "_make_overlay_s3_client", lambda: _FakeS3())
    monkeypatch.setattr(gp, "_probe_s3_duration_sec", lambda **kwargs: 4.0)

    monkeypatch.setenv("MODE", "dev")
    monkeypatch.setenv("AUDIO_FILE_PATH", str(dummy_audio))
    monkeypatch.setenv("AUDIO_DIR", str(dummy_audio.parent))
    monkeypatch.setenv("AUDIO_FILE_NAME", "audio_source.mp3")
    monkeypatch.setenv("OVERLAY_ENABLED", "1")
    monkeypatch.setenv("OVERLAY_SOURCE_MODE", "s3_prefix")
    monkeypatch.setenv("OVERLAY_S3_BUCKET", "bucket")
    monkeypatch.setenv("OVERLAY_S3_PREFIX", "overlays/")
    monkeypatch.setenv("OVERLAY_MATCH_MODE", "global")
    monkeypatch.setenv("OVERLAY_SELECTION_SEED", "seed-s3")
    monkeypatch.delenv("OVERLAY_INVENTORY_JSON", raising=False)

    out_dir = tmp_path / "out"
    data_dir = tmp_path / "data"
    render_all_steps(
        repo_root=repo_root,
        plan=_plan(100.0, 115.0),
        footage_inventory_json=inv_path,
        out_dir=out_dir,
        data_dir=data_dir,
    )

    footage_cfg = json.loads((out_dir / "footage_config.json").read_text(encoding="utf-8"))
    overlays = [x for x in footage_cfg.get("layers", []) if isinstance(x, dict) and x.get("type") == "overlay"]
    assert len(overlays) == 4
    ov = overlays[0]
    assert str(ov.get("file_path", "")).startswith("s3://bucket/overlays/")
    assert float(ov.get("in_point", 0.0)) == 0.0
    assert float(overlays[-1].get("out_point", 0.0)) == 15.0
    for i in range(len(overlays) - 1):
        assert abs(float(overlays[i]["out_point"]) - float(overlays[i + 1]["in_point"])) <= 1e-6


def test_overlay_blueprint_uses_opacity_from_env(monkeypatch) -> None:
    monkeypatch.setenv("OVERLAY_OPACITY", "35")
    cfg = {
        "text_dur_hint": 10.0,
        "layers": [
            {
                "type": "overlay",
                "name": "ov",
                "file_name": "ov.mp4",
                "file_path": "s3://bucket/pinterest_collection/Rock/dark_forest/overlays/ov.mp4",
                "src_w": 1080,
                "src_h": 1920,
                "in_point": 0.0,
                "out_point": 10.0,
                "start_time": 0.0,
                "enabled": True,
            },
            {
                "type": "footage",
                "name": "bg",
                "file_name": "bg.mp4",
                "file_path": "s3://bucket/pinterest_collection/Rock/dark_forest/bg.mp4",
                "src_w": 720,
                "src_h": 1280,
                "in_point": 0.0,
                "out_point": 10.0,
                "start_time": 0.0,
                "enabled": True,
            },
        ],
    }
    layers = build_footage_layers(
        repo_root=Path("."),
        footage_cfg=cfg,
        main_comp_name="Comp 1",
        text_comp_name="Text",
    )

    overlay_layers = []
    for it in layers:
        td = it.get("text_data") if isinstance(it.get("text_data"), dict) else {}
        meta = td.get("layer_meta") if isinstance(td.get("layer_meta"), dict) else {}
        if bool(meta.get("isOverlay")):
            overlay_layers.append(it)

    assert len(overlay_layers) == 1
    overlay = overlay_layers[0]
    tf_opacity = overlay.get("props", {}).get("tf_opacity", {})
    assert float(tf_opacity.get("value")) == 35.0


def test_overlay_tiling_exceeds_repeat_limit_raises() -> None:
    asset = {
        "file_name": "ov.mp4",
        "file_path": "s3://bucket/overlays/ov.mp4",
        "src_w": 1080,
        "src_h": 1920,
        "duration_sec": 0.01,
    }
    with pytest.raises(RuntimeError, match="exceeded limit=100"):
        gp.build_overlay_tiled_layers(overlay_asset=asset, clip_dur=2.0)


def test_overlay_duration_missing_and_probe_fails_raises(monkeypatch) -> None:
    monkeypatch.setattr(gp, "_probe_s3_duration_sec", lambda **kwargs: None)
    asset = {
        "file_name": "ov.mp4",
        "file_path": "s3://bucket/overlays/ov.mp4",
        "src_w": 1080,
        "src_h": 1920,
        "duration_sec": None,
    }
    with pytest.raises(RuntimeError, match="overlay duration probe failed"):
        gp.build_overlay_tiled_layers(overlay_asset=asset, clip_dur=15.0)
