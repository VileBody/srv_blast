# -*- coding: utf-8 -*-
"""End-to-end (pure-Python) test for the F3 media plumbing:
asset_picker._media -> project_builder._extract_f3_media -> payload['f3_media']
-> render_manifest.collect_media_urls_from_render_payload -> media[]."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.project_builder import _extract_f3_media
from services.orchestrator.render_manifest import collect_media_urls_from_render_payload


def test_extract_f3_media_filters_garbage():
    cfg = {
        "f3": {
            "_media": [
                {"url": "s3://b/sounds/a.wav", "relpath": "media/audio/a.wav"},
                {"url": "", "relpath": "media/audio/skip.wav"},          # empty url
                {"url": "s3://b/x", "relpath": ""},                       # empty rel
                {"url": "s3://b/sounds/a.wav", "relpath": "media/audio/a.wav"},  # dedup
                "not-a-dict",
                {"url": "s3://b/img/l.png", "relpath": "/media/img/l.png"},  # leading slash stripped
            ]
        }
    }
    out = _extract_f3_media(cfg)
    assert out == [
        {"url": "s3://b/sounds/a.wav", "relpath": "media/audio/a.wav"},
        {"url": "s3://b/img/l.png",    "relpath": "media/img/l.png"},
    ]


def test_extract_f3_media_no_block():
    assert _extract_f3_media({}) == []
    assert _extract_f3_media({"f3": {}}) == []
    assert _extract_f3_media({"f3": {"_media": "not-a-list"}}) == []


def test_collect_media_urls_appends_f3_media(tmp_path: Path):
    payload = {
        "footage_layers": [
            {
                "text_data": {
                    "source_footage": {
                        "file_name": "clip_01.mp4",
                        "remote_url": "https://cdn/clip_01.mp4",
                    }
                }
            }
        ],
        "f3_media": [
            {"url": "s3://fx/fx_assets/sounds/camera_flash/flash_a.wav", "relpath": "media/audio/flash_a.wav"},
            {"url": "s3://fx/fx_assets/logo/group_1245.png", "relpath": "media/img/group_1245.png"},
            # invalid url scheme should be skipped
            {"url": "/local/path", "relpath": "media/audio/local.wav"},
        ],
    }
    p = tmp_path / "render_payload.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    # audio_url empty => skip the audio_only injection branch
    out = collect_media_urls_from_render_payload(p, audio_url="")
    relpaths = sorted(item["relpath"] for item in out)
    assert relpaths == [
        "media/audio/flash_a.wav",
        "media/img/group_1245.png",
        "media/video/clip_01.mp4",
    ]
    by_rel = {it["relpath"]: it["url"] for it in out}
    assert by_rel["media/audio/flash_a.wav"].startswith("s3://fx/")
    assert by_rel["media/img/group_1245.png"].startswith("s3://fx/")


def test_collect_media_urls_dedup_against_footage(tmp_path: Path):
    """If an F3 asset has the same relpath as a footage entry, footage wins
    (footage runs first in collect_media_urls)."""
    payload = {
        "footage_layers": [
            {
                "text_data": {
                    "source_footage": {
                        "file_name": "shared.png",
                        "remote_url": "https://cdn/footage.png",
                    }
                }
            }
        ],
        "f3_media": [
            {"url": "s3://fx/logo/shared.png", "relpath": "media/video/shared.png"},
        ],
    }
    p = tmp_path / "render_payload.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    out = collect_media_urls_from_render_payload(p, audio_url="")
    rels = [it["relpath"] for it in out]
    assert rels.count("media/video/shared.png") == 1
    assert out[0]["url"].startswith("https://cdn")
