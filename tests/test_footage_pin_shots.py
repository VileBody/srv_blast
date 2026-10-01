"""Смонтированный пин → отдельный клип на каждый план (виртуальные сегменты одного файла)."""
from __future__ import annotations

import json

from mlcore.footage_segments import expand_shot_rows, shot_bounds
from mlcore.models.footage_plan import FootageClipPick
from mlcore.storyboard_plan import _pinned_clip

PIN = {"file_name": "123456789.mp4", "genre": "Alone_Girls", "tag": "night", "src_w": 720, "src_h": 1280,
       "duration_sec": 10.0, "file_path": "s3://b/p/123456789.mp4"}


def test_shots_split_exactly_on_edits_and_drop_flashes():
    assert shot_bounds(10.0, [2.5, 3.0, 7.0], min_shot=1.0) == [(0.0, 2.5), (3.0, 7.0), (7.0, 10.0)]


def test_edited_pin_becomes_one_clip_per_shot_of_the_same_file():
    rows = expand_shot_rows([{**PIN, "scene_cuts": [2.5, 3.0, 7.0]}], min_shot=1.0)
    assert [r["file_name"] for r in rows] == ["123456789~seg00.mp4", "123456789~seg01.mp4", "123456789~seg02.mp4"]
    assert [(r["segment_base_sec"], r["duration_sec"]) for r in rows] == [(0.0, 2.5), (3.0, 4.0), (7.0, 3.0)]
    assert {r["media_file_name"] for r in rows} == {"123456789.mp4"}
    assert {r["file_path"] for r in rows} == {PIN["file_path"]}  # один файл, одна загрузка
    assert all(r["genre"] == "Alone_Girls" and r["tag"] == "night" for r in rows)  # теги пина


def test_pins_without_edits_and_existing_segments_pass_through():
    plain = dict(PIN)
    seg = {**PIN, "file_name": "x~seg01.mp4", "segment_base_sec": 20.0, "scene_cuts": [25.0]}
    assert expand_shot_rows([plain, seg]) == [plain, seg]


def test_expansion_is_idempotent():
    once = expand_shot_rows([{**PIN, "scene_cuts": [4.0]}], min_shot=1.0)
    assert expand_shot_rows(once, min_shot=1.0) == once


def test_pin_of_only_flashes_leaves_the_pool():
    assert expand_shot_rows([{**PIN, "duration_sec": 2.0, "scene_cuts": [0.5, 1.0, 1.5]}], min_shot=1.0) == []


def test_picker_keeps_a_shot_clip_inside_its_shot():
    from mlcore.footage_picker import _source_offset_for_asset

    shot = expand_shot_rows([{**PIN, "scene_cuts": [2.5, 7.0]}], min_shot=1.0)[1]  # [2.5, 7.0)
    for idx in range(40):
        o = _source_offset_for_asset(asset=shot, file_name=shot["file_name"], interval_len=1.5,
                                     seed_value=3, interval_idx=idx, offset_enabled=True)
        assert 2.5 <= o and o + 1.5 <= 7.0


def test_pinned_replacement_plays_its_own_shot_not_the_file_start():
    """Замена клипа на столе: рендер обязан начать с окна выбранного плана."""
    shot = expand_shot_rows([{**PIN, "scene_cuts": [2.5, 7.0]}], min_shot=1.0)[1]
    picked = FootageClipPick(file_name="other.mp4", fit_mode="cover", in_point=12.0, out_point=13.5,
                             start_time=11.0, source_offset_sec=1.0)
    pinned = _pinned_clip(picked, shot["file_name"], shot)
    assert pinned.source_offset_sec == 2.5
    assert pinned.start_time == 12.0 - 2.5
    plain = _pinned_clip(picked, "plain.mp4", {"file_name": "plain.mp4"})
    assert plain.source_offset_sec == 0.0 and plain.start_time == 12.0


def test_video_inventory_splits_edited_pins(tmp_path, monkeypatch):
    from footage_config import build_inventory_and_bundle

    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("S3_BUCKET_ASSET_STORAGE", "bucket")
    monkeypatch.setenv("S3_ASSET_PREFIX", "pins")
    monkeypatch.setenv("FOOTAGE_S3_PREFLIGHT_MODE", "off")
    row = {k: PIN[k] for k in ("genre", "tag", "src_w", "src_h", "duration_sec")}
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"assets": [{**row, "file_name": "111111111.mp4", "scene_cuts": [4.0]},
                                            {**row, "file_name": "222222222.mp4"}]}), encoding="utf-8")

    def build(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        out = tmp_path / "inventory.json"
        build_inventory_and_bundle(repo_root=tmp_path, footage_dir=tmp_path / "footage", static_assets_index_path=index,
                                   inventory_out_path=out, bundle_out_path=tmp_path / "bundle.json")
        return {a["file_name"]: a for a in json.loads(out.read_text(encoding="utf-8"))["assets"]}

    assets = build()
    assert sorted(assets) == ["111111111~seg00.mp4", "111111111~seg01.mp4", "222222222.mp4"]
    assert assets["111111111~seg01.mp4"]["segment_base_sec"] == 4.0
    assert assets["111111111~seg01.mp4"]["file_path"].endswith("/111111111.mp4")
    assert sorted(build(FOOTAGE_SPLIT_PIN_SHOTS="0")) == ["111111111.mp4", "222222222.mp4"]
