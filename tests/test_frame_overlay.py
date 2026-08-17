# -*- coding: utf-8 -*-
"""Рамка: каталог → JSX-оверлей → media[] и место в шаблоне.

Рамка — не хук: она ложится поверх ВСЕХ слоёв (включая субтитры) и не требует
дропа. Тесты фиксируют именно это: инъекция идёт последней, ассет уезжает в
download-список ноды, а без S3-конфига всё вырождается в no-op без падения.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.project_builder import _build_frame_overlay_js, _extract_frame_media
from app.render_plan import build_visual_ops
from mlcore.hooks.frames import catalog
from mlcore.hooks.frames.overlay import build_overlay_jsx


ROOT = Path(__file__).resolve().parents[1]


# ---------- каталог ----------


def test_frame_ids_match_the_schema_literal():
    """Дрифт id-сета каталога vs schemas.frame_id ловим здесь, а не в проде."""
    src = io.open(ROOT / "services" / "orchestrator" / "schemas.py", encoding="utf-8").read()
    for fid in catalog.FRAME_IDS:
        assert f'"{fid}"' in src, fid


def test_resolve_needs_the_asset_bucket(monkeypatch):
    monkeypatch.delenv("FX_ASSETS_S3_BUCKET", raising=False)
    assert catalog.resolve_frame_asset("rounded") is None


def test_resolve_builds_s3_url_and_relpath(monkeypatch):
    monkeypatch.setenv("FX_ASSETS_S3_BUCKET", "fx-bucket")
    monkeypatch.setenv("FX_ASSETS_S3_PREFIX", "fx_assets")
    got = catalog.resolve_frame_asset("letterbox")
    assert got == {
        "relpath": "media/img/group_2173.png",
        "url": "s3://fx-bucket/fx_assets/frames/group_2173.png",
    }


@pytest.mark.parametrize("bad", ["", "none", "does_not_exist"])
def test_resolve_rejects_non_frames(monkeypatch, bad):
    monkeypatch.setenv("FX_ASSETS_S3_BUCKET", "fx-bucket")
    assert catalog.resolve_frame_asset(bad) is None


# ---------- JSX-билдер ----------


def test_overlay_bakes_the_asset_path():
    js = build_overlay_jsx(frame_id="rounded", asset_relpath="media/img/exclude.png")
    assert 'framePath: (String(__APP_DIR || "") + "/" + "media/img/exclude.png")' in js
    # скрипт инлайнится целиком
    assert "frame_overlay" in js
    assert "moveToBeginning" in js


def test_overlay_without_asset_is_noop():
    assert build_overlay_jsx(frame_id="rounded", asset_relpath=None) == ""
    assert build_overlay_jsx(frame_id="", asset_relpath="media/img/x.png") == ""


def test_overlay_rejects_unknown_id():
    with pytest.raises(ValueError):
        build_overlay_jsx(frame_id="nope", asset_relpath="media/img/x.png")


def test_overlay_binds_the_named_comp():
    js = build_overlay_jsx(
        frame_id="rounded", asset_relpath="media/img/exclude.png", comp_var="PHOTO_COMP"
    )
    assert "var __fr_comp = PHOTO_COMP;" in js


# ---------- провязка в билдере ----------


def test_builder_noop_without_block():
    assert _build_frame_overlay_js({}) == ""
    assert _build_frame_overlay_js({"frame": {}}) == ""
    assert _build_frame_overlay_js({"frame": {"frame_id": "rounded"}}) == ""


def test_builder_emits_overlay_from_block():
    js = _build_frame_overlay_js(
        {"frame": {"frame_id": "soft_bars", "relpath": "media/img/group_2172.png"}}
    )
    assert "media/img/group_2172.png" in js


def test_frame_media_is_a_node_download_entry():
    block = {
        "frame": {
            "frame_id": "rounded",
            "relpath": "media/img/exclude.png",
            "url": "s3://fx-bucket/fx_assets/frames/exclude.png",
        }
    }
    assert _extract_frame_media(block) == [
        {"url": "s3://fx-bucket/fx_assets/frames/exclude.png", "relpath": "media/img/exclude.png"}
    ]
    # без url (не разрезолвили ассет) — скачивать нечего
    assert _extract_frame_media({"frame": {"frame_id": "rounded", "relpath": "media/img/a.png"}}) == []
    assert _extract_frame_media({}) == []


def test_frame_survives_the_visual_ops_round_trip():
    """config → visual op → обратно в config: без этого рамка терялась бы, как
    чуть не потерялся seed у f3."""
    cfg = {"frame": {"frame_id": "letterbox", "relpath": "media/img/group_2173.png"}}
    ops = build_visual_ops(subtitles_mode="", full_edit_config=cfg, f3_media=[])
    frame_ops = [op for op in ops if op.type == "overlay.frame.v1"]
    assert len(frame_ops) == 1
    assert frame_ops[0].params["frame_id"] == "letterbox"
    assert frame_ops[0].assets[0].path == "media/img/group_2173.png"


def test_no_frame_no_op_in_visual_ops():
    ops = build_visual_ops(subtitles_mode="", full_edit_config={}, f3_media=[])
    assert not [op for op in ops if op.type == "overlay.frame.v1"]


# ---------- место в шаблоне ----------


def test_frame_token_is_injected_after_the_subtitles_and_f4_raise():
    tpl = io.open(ROOT / "templates" / "project_template.j2", encoding="utf-8").read()
    assert "{{ frame_overlay_js }}" in tpl
    assert tpl.index("{{ jsx_subtitles_js }}") < tpl.index("{{ frame_overlay_js }}")
    assert tpl.index("__F4_OVERLAY__") < tpl.index("{{ frame_overlay_js }}")
    # и строго до сохранения проекта
    assert tpl.index("{{ frame_overlay_js }}") < tpl.index("project.save(aepFile)")
