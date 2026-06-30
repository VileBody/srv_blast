# -*- coding: utf-8 -*-
"""Parity test: tg_bot_public mirrors the footage bucket-preview infra of
tg_bot_botapi (precision flow, phase 4).

The vibe shortlist sends a captionless preview reel per bucket. The lookup infra
(_load_bucket_previews / _bucket_preview_file_id / _vibe_display_label) is mirrored
into both bots; the only difference is which file_id field each sends — the team
bot sends `file_id`, the public bot sends `file_id_public` (captured per bot).
"""
from __future__ import annotations

import pytest

team = pytest.importorskip("services.tg_bot_botapi.app")
pub = pytest.importorskip("services.tg_bot_public.app")


def test_preview_helpers_exist_in_both_bots():
    for mod in (team, pub):
        assert callable(getattr(mod, "_load_bucket_previews", None))
        assert callable(getattr(mod, "_bucket_preview_file_id", None))
        assert callable(getattr(mod, "_vibe_display_label", None))


def test_file_id_field_differs_team_vs_public():
    assert team._BUCKET_PREVIEW_FILE_ID_FIELD == "file_id"
    assert pub._BUCKET_PREVIEW_FILE_ID_FIELD == "file_id_public"


def test_display_label_slash_to_comma_consistent():
    for mod in (team, pub):
        assert mod._vibe_display_label("Природа / закат") == "Природа, закат"
        assert mod._vibe_display_label("no slash") == "no slash"


def test_bucket_preview_file_id_reads_per_bot_field(monkeypatch):
    store = {"romance_major:nature_sunset": {"file_id": "TEAM_FID", "file_id_public": "PUB_FID"}}
    monkeypatch.setattr(team, "_BUCKET_PREVIEWS_CACHE", store, raising=False)
    monkeypatch.setattr(pub, "_BUCKET_PREVIEWS_CACHE", store, raising=False)
    assert team._bucket_preview_file_id("romance_major:nature_sunset") == "TEAM_FID"
    assert pub._bucket_preview_file_id("romance_major:nature_sunset") == "PUB_FID"
    assert team._bucket_preview_file_id("unknown:bucket") == ""


def test_previews_path_points_at_repo_data():
    for mod in (team, pub):
        p = mod._bucket_previews_path()
        assert p.name == "footage_bucket_previews.json"
        assert p.parent.name == "data"


def test_hook_preview_helpers_exist_and_path():
    for mod in (team, pub):
        assert callable(getattr(mod, "_load_hook_previews", None))
        assert callable(getattr(mod, "_hook_preview_file_id", None))
        p = mod._hook_previews_path()
        assert p.name == "hook_previews.json"
        assert p.parent.name == "data"


def test_hook_preview_file_id_reads_per_bot_field(monkeypatch):
    store = {"motion:swipe": {"file_id": "TEAM_FID", "file_id_public": "PUB_FID"}}
    monkeypatch.setattr(team, "_HOOK_PREVIEWS_CACHE", store, raising=False)
    monkeypatch.setattr(pub, "_HOOK_PREVIEWS_CACHE", store, raising=False)
    assert team._hook_preview_file_id("motion:swipe") == "TEAM_FID"
    assert pub._hook_preview_file_id("motion:swipe") == "PUB_FID"
    assert team._hook_preview_file_id("nope:x") == ""
