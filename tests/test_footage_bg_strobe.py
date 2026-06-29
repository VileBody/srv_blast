# -*- coding: utf-8 -*-
"""BG_MODE=solid_strobe: B/W bg flipping on scene cuts + flash + Difference text."""
from __future__ import annotations

from pathlib import Path

from app.footage_comp import build_footage_layers


def _cfg():
    # 3 footage clips → cuts at 1.0 and 2.5; clip ends at 4.0.
    return {
        "main_comp_w": 1080, "main_comp_h": 1920, "text_dur_hint": 4.0,
        "layers": [
            {"type": "footage", "in_point": 0.0, "out_point": 1.0, "file_name": "a.mp4"},
            {"type": "footage", "in_point": 1.0, "out_point": 2.5, "file_name": "b.mp4"},
            {"type": "footage", "in_point": 2.5, "out_point": 4.0, "file_name": "c.mp4"},
            {"type": "audio_only", "name": "реф", "start_time": 0.0, "in_point": 0.0, "out_point": 4.0,
             "file_name": "track.mp3",
             "source_footage": {"file_name": "track.mp3", "remote_url": "s3://x/track.mp3"}},
        ],
    }


def test_strobe_builds_bw_segments_flash_and_difference(monkeypatch):
    monkeypatch.setenv("BG_MODE", "solid_strobe")
    out = build_footage_layers(
        repo_root=Path("."), footage_cfg=_cfg(),
        main_comp_name="Comp 1", text_comp_name="Текст",
    )
    names = [l["name"] for l in out]
    # no real footage in strobe mode
    assert not any(n in ("a.mp4", "b.mp4", "c.mp4") for n in names)
    # 3 bg segments (2 cuts → 3 segments) + 2 flashes
    segs = [l for l in out if l["name"].startswith("strobe_bg_")]
    flashes = [l for l in out if l["name"].startswith("strobe_flash_")]
    assert len(segs) == 3
    assert len(flashes) == 2

    # segments alternate white→black→white
    by_name = {l["name"]: l for l in segs}
    assert by_name["strobe_bg_0"]["text_data"]["solid_source"]["color_rgb01"] == [1.0, 1.0, 1.0]
    assert by_name["strobe_bg_1"]["text_data"]["solid_source"]["color_rgb01"] == [0.0, 0.0, 0.0]
    assert by_name["strobe_bg_2"]["text_data"]["solid_source"]["color_rgb01"] == [1.0, 1.0, 1.0]

    # TEXT precomp set to Difference blend (auto-invert)
    txt = next(l for l in out if l["type"] == "precomp")
    assert txt["text_data"]["layer_meta"]["blendingModeCode"] == "difference"

    # flashes are white and sit above bg, below text (z 150 < 200, > 1)
    for fl in flashes:
        assert fl["text_data"]["solid_source"]["color_rgb01"] == [1.0, 1.0, 1.0]
        assert fl["z_index"] == 150
