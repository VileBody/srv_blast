# -*- coding: utf-8 -*-
"""The video tagger must ask "what is tagged" scoped to the VIDEO pool.

Unscoped, a clip_id that carries only a PHOTO tag row reads as already tagged and
its video is skipped forever. That is exactly the set the old single-column
primary key overwrote (221 clips), so an unscoped question makes the re-tag a
silent no-op for the only clips that need it.
"""
from __future__ import annotations

import sys
import types
from typing import Any, Dict, List

import pytest

from mlcore import footage_tagger


class _Conn:
    async def close(self) -> None:
        return None


def _install_fake_asyncpg(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("asyncpg")

    async def _connect(*_a: Any, **_kw: Any) -> _Conn:
        return _Conn()

    mod.connect = _connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "asyncpg", mod)


def test_default_untagged_lookup_is_scoped_to_the_video_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_asyncpg(monkeypatch)
    seen: List[Dict[str, Any]] = []

    from mlcore import footage_tags_db

    async def _fake_init_schema(_conn: Any) -> None:
        return None

    async def _fake_fetch(_conn: Any, *, source: Any = None) -> set:
        seen.append({"source": source})
        return set()

    monkeypatch.setattr(footage_tags_db, "init_schema", _fake_init_schema)
    monkeypatch.setattr(footage_tags_db, "fetch_tagged_clip_ids", _fake_fetch)

    footage_tagger.run_tagging_batch(
        bucket="b",
        source_prefix="pinterest_collection",
        db_url="postgres://stub",
        list_keys_fn=lambda: [],
        tag_fn=lambda _k: None,
        upsert_fn=lambda _r: 0,
        # fetch_tagged_fn deliberately left at its default — that closure is what
        # production runs and what this test exists to pin.
    )

    assert seen == [{"source": footage_tags_db.SOURCE_VIDEO}]
