# -*- coding: utf-8 -*-
"""A collection job must not try to load tag metadata.

Caught on the first live run, after every unit test passed:

    RuntimeError('FOOTAGE_STYLE_METADATA_DB_PATHS_JSON must be a non-empty JSON list')

The job env deliberately carries an EMPTY db-path list to say "this pool has no
tags". The resolver treats an empty list as a misconfigured env and raises —
correct for the tagged pools, fatal here. The two pieces were each right and
were never exercised together, because nothing had run a real collection job.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mlcore.gemini_orchestrator import (
    _collection_plane_active,
    load_style_metadata_index,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOOTAGE_ROTATION_THEME", raising=False)
    monkeypatch.delenv("FOOTAGE_STYLE_METADATA_DB_PATHS_JSON", raising=False)


def test_the_plane_is_read_from_what_actually_routes_the_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _collection_plane_active() is False
    monkeypatch.setenv("FOOTAGE_ROTATION_THEME", "collection")
    assert _collection_plane_active() is True
    monkeypatch.setenv("FOOTAGE_ROTATION_THEME", "romance_minor")
    assert _collection_plane_active() is False


def test_the_empty_db_list_a_collection_job_carries_is_survivable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Exactly what tasks.py puts in the env for a collection job.
    monkeypatch.setenv("FOOTAGE_ROTATION_THEME", "collection")
    monkeypatch.setenv("FOOTAGE_STYLE_METADATA_DB_PATHS_JSON", json.dumps([]))
    assert load_style_metadata_index(root=tmp_path, collection_plane=True) == {}


def test_the_tagged_pool_still_rejects_an_empty_db_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The guard exists to catch a misconfigured env and must not be loosened.
    monkeypatch.setenv("FOOTAGE_STYLE_METADATA_DB_PATHS_JSON", json.dumps([]))
    with pytest.raises(RuntimeError, match="non-empty JSON list"):
        load_style_metadata_index(root=tmp_path, collection_plane=False)


def test_a_missing_db_file_still_fails_loudly_on_the_tagged_pool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "FOOTAGE_STYLE_METADATA_DB_PATHS_JSON", json.dumps([str(tmp_path / "nope.json")])
    )
    with pytest.raises(FileNotFoundError):
        load_style_metadata_index(root=tmp_path, collection_plane=False)
