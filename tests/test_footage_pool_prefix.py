"""The footage pool prefix must have ONE resolver, shared by the manual index
builder and the activation task, so index-scan == tag-scan == Asset-UI browse.
Guards against the narrow/broad prefix-collision footgun.
"""
from __future__ import annotations

import pytest

from scripts.build_static_assets_index import resolve_pool_source_prefix


def test_explicit_override_wins(monkeypatch):
    monkeypatch.setenv("ASSET_UI_SOURCE_PREFIX", "custom_pool")
    monkeypatch.setenv("S3_ASSET_PREFIX", "pinterest_collection/pins2_1to1_20260323")
    assert resolve_pool_source_prefix() == "custom_pool"


def test_top_level_of_s3_asset_prefix(monkeypatch):
    monkeypatch.delenv("ASSET_UI_SOURCE_PREFIX", raising=False)
    monkeypatch.setenv("S3_ASSET_PREFIX", "pinterest_collection/pins2_1to1_20260323")
    # the pool is the whole collection, not one dated 1:1 subfolder
    assert resolve_pool_source_prefix() == "pinterest_collection"


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv("ASSET_UI_SOURCE_PREFIX", raising=False)
    monkeypatch.delenv("S3_ASSET_PREFIX", raising=False)
    assert resolve_pool_source_prefix() == "pinterest_collection"


def test_strips_slashes(monkeypatch):
    monkeypatch.delenv("ASSET_UI_SOURCE_PREFIX", raising=False)
    monkeypatch.setenv("S3_ASSET_PREFIX", "/pinterest_collection/selected/")
    assert resolve_pool_source_prefix() == "pinterest_collection"


def test_asset_ui_browse_matches_resolver(monkeypatch):
    """asset_routes._asset_ui_source_prefix must resolve identically (no drift)."""
    ar = pytest.importorskip("services.orchestrator.asset_routes")
    for s3p in ("pinterest_collection/pins2_1to1_20260323", "pinterest_collection/selected", ""):
        monkeypatch.delenv("ASSET_UI_SOURCE_PREFIX", raising=False)
        if s3p:
            monkeypatch.setenv("S3_ASSET_PREFIX", s3p)
        else:
            monkeypatch.delenv("S3_ASSET_PREFIX", raising=False)
        assert ar._asset_ui_source_prefix() == resolve_pool_source_prefix()
