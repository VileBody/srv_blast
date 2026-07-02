"""Snapshot pool-filter: orphan tags of deleted clips must be dropped from the
exported snapshot, but never blank it when the pool registry is empty."""
from __future__ import annotations

from mlcore.footage_tags_db import filter_snapshot_to_pool


def _row(clip_num, tags=("night",)):
    return {"video_key": f"{clip_num}.mp4", "video_path": f"{clip_num}.mp4", "theme_tags": list(tags)}


def test_drops_orphans_not_in_pool():
    rows = [_row("10000001"), _row("10000002"), _row("10000003")]
    pool = {"10000001", "10000003"}  # 10000002 was deleted
    out = filter_snapshot_to_pool(rows, pool)
    keys = {r["video_key"] for r in out}
    assert keys == {"10000001.mp4", "10000003.mp4"}


def test_failsafe_empty_pool_returns_all():
    rows = [_row("10000001"), _row("10000002")]
    assert len(filter_snapshot_to_pool(rows, set())) == 2   # registry empty -> no-op
    assert len(filter_snapshot_to_pool(rows, None)) == 2


def test_photo_namespaced_ids_pass_through_when_pooled():
    rows = [{"video_key": "sunset.jpg", "theme_tags": ["warm"]}]
    # photo clip_id is namespaced 'photo:sunset'
    assert filter_snapshot_to_pool(rows, {"photo:sunset"}) == rows
    # not in pool -> dropped
    assert filter_snapshot_to_pool(rows, {"photo:other"}) == []
