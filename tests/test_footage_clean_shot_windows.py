"""Окно клипа внутри одного плана: внутренний стык пина не должен попадать в ролик."""
from __future__ import annotations

import pytest

from mlcore.footage_picker import _SOURCE_OFFSET_SAFETY, _source_offset_for_asset

GUARD = _SOURCE_OFFSET_SAFETY


def _offset(asset, *, interval_len, idx=0, seed=7, enabled=True, name="pin.mp4"):
    return _source_offset_for_asset(asset=asset, file_name=name, interval_len=interval_len,
                                    seed_value=seed, interval_idx=idx, offset_enabled=enabled)


def _crosses(offset, interval_len, cuts):
    return any(offset < c < offset + interval_len for c in cuts)


@pytest.mark.parametrize("interval_len", [0.5, 1.0, 1.5, 2.2, 3.0])
def test_window_never_spans_an_internal_edit(interval_len):
    cuts = [2.0, 3.1, 6.5, 9.0]
    asset = {"file_name": "pin.mp4", "duration_sec": 14.0, "scene_cuts": cuts}
    for idx in range(60):
        o = _offset(asset, interval_len=interval_len, idx=idx)
        assert not _crosses(o, interval_len, cuts), (idx, o)
        assert o + interval_len <= 14.0


def test_window_may_open_right_after_an_edit_but_not_on_the_edit_frame():
    asset = {"file_name": "pin.mp4", "duration_sec": 6.0, "scene_cuts": [3.0]}
    # 2.9 с влезают в первый план [0, 3.0) впритык: окно начинается с начала файла
    assert _offset(asset, interval_len=2.9, enabled=False) == pytest.approx(0.0)
    # 5 с влезают только во второй план [0.5, 6.0): окно открывается на кадр позже стыка
    o = _offset({**asset, "scene_cuts": [0.5]}, interval_len=5.0, enabled=False)
    assert o == pytest.approx(0.5 + GUARD)


def test_spread_covers_every_clean_shot():
    cuts = [4.0, 8.0]
    asset = {"file_name": "pin.mp4", "duration_sec": 12.0, "scene_cuts": cuts}
    shots = {int(_offset(asset, interval_len=1.5, idx=i) // 4) for i in range(80)}
    assert shots == {0, 1, 2}


def test_recomputed_per_interval_length():
    """Сетка склеек и скорость меняют длину отрезка — окно считается под неё заново."""
    asset = {"file_name": "pin.mp4", "duration_sec": 10.0, "scene_cuts": [1.0, 2.0, 7.5]}
    short = _offset(asset, interval_len=0.8, idx=3)
    long = _offset(asset, interval_len=5.0, idx=3)
    assert not _crosses(short, 0.8, [1.0, 2.0, 7.5])
    assert 2.0 < long and long + 5.0 <= 7.5   # 5 с влезают только в план [2.0, 7.5]


def test_no_shot_long_enough_keeps_plain_jitter():
    asset = {"file_name": "pin.mp4", "duration_sec": 6.0, "scene_cuts": [1.0, 2.0, 3.0, 4.0, 5.0]}
    without_cuts = {k: v for k, v in asset.items() if k != "scene_cuts"}
    assert _offset(asset, interval_len=2.0, idx=5) == _offset(without_cuts, interval_len=2.0, idx=5)


def test_without_edits_behaviour_is_unchanged():
    asset = {"file_name": "pin.mp4", "duration_sec": 9.0}
    assert _offset({**asset, "scene_cuts": []}, interval_len=1.5, idx=2) == _offset(asset, interval_len=1.5, idx=2)


def test_deterministic():
    asset = {"file_name": "pin.mp4", "duration_sec": 14.0, "scene_cuts": [2.0, 6.0]}
    assert _offset(asset, interval_len=1.2, idx=4) == _offset(asset, interval_len=1.2, idx=4)


def test_virtual_segment_uses_file_relative_edits_inside_its_window():
    # сегмент [20, 40) длинного файла, стыки — в секундах файла
    asset = {"file_name": "film~seg01.mp4", "duration_sec": 20.0, "segment_base_sec": 20.0,
             "scene_cuts": [5.0, 24.0, 31.0, 45.0]}
    for idx in range(40):
        o = _offset(asset, interval_len=3.0, idx=idx, name="film~seg01.mp4")
        assert 20.0 <= o and o + 3.0 <= 40.0
        assert not _crosses(o, 3.0, [24.0, 31.0])


def test_jitter_off_takes_earliest_clean_position():
    asset = {"file_name": "pin.mp4", "duration_sec": 10.0, "scene_cuts": [0.4, 6.0]}
    assert _offset(asset, interval_len=3.0, enabled=False) == pytest.approx(0.4 + GUARD)


def test_pin_edits_reach_the_picker_inventory(tmp_path, monkeypatch):
    """Стыки из индекса (footage_assets.scene_cuts) доходят до инвентаря пикера и у пинов."""
    import json

    from footage_config import build_inventory_and_bundle

    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("S3_BUCKET_ASSET_STORAGE", "bucket")
    monkeypatch.setenv("S3_ASSET_PREFIX", "pins")
    monkeypatch.setenv("FOOTAGE_S3_PREFLIGHT_MODE", "off")
    row = {"genre": "Alternative", "tag": "night", "src_w": 720, "src_h": 1280, "duration_sec": 14.0}
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"assets": [
        {**row, "file_name": "edited.mp4", "scene_cuts": [2.5, 7.0]},
        {**row, "file_name": "single.mp4"},
    ]}), encoding="utf-8")
    inv_out = tmp_path / "inventory.json"
    build_inventory_and_bundle(repo_root=tmp_path, footage_dir=tmp_path / "footage", static_assets_index_path=index,
                               inventory_out_path=inv_out, bundle_out_path=tmp_path / "bundle.json")
    assets = {a["file_name"]: a for a in json.loads(inv_out.read_text(encoding="utf-8"))["assets"]}
    # смонтированный пин режется на планы (test_footage_pin_shots), стыки едут на каждом
    shots = [a for name, a in assets.items() if name.startswith("edited~seg")]
    assert len(shots) == 3 and all(a["scene_cuts"] == [2.5, 7.0] for a in shots)
    assert "scene_cuts" not in assets["single.mp4"]
