from __future__ import annotations

import time
from pathlib import Path

from core.filesystem_hygiene import cleanup_jobs_artifacts, cleanup_tmp_chat_dirs, parse_glob_allowlist


def _touch(path: Path, *, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    path.touch()
    path.stat()
    import os

    os.utime(path, (mtime, mtime))


def test_parse_glob_allowlist_deduplicates_and_strips() -> None:
    assert parse_glob_allowlist(" a.txt, b*.json, a.txt ,") == ("a.txt", "b*.json")


def test_cleanup_tmp_chat_dirs_respects_per_subdir_ttl(tmp_path: Path) -> None:
    now = time.time()
    root = tmp_path / "tg_tmp"

    old_incoming = root / "42" / "incoming" / "old.wav"
    fresh_incoming = root / "42" / "incoming" / "new.wav"
    old_prepared = root / "42" / "prepared" / "old.mp3"
    fresh_result = root / "42" / "result" / "fresh.mp4"

    _touch(old_incoming, mtime=now - 7200)
    _touch(fresh_incoming, mtime=now - 30)
    _touch(old_prepared, mtime=now - 7200)
    _touch(fresh_result, mtime=now - 30)

    stats = cleanup_tmp_chat_dirs(
        tmp_root=root,
        retention_by_subdir_s={
            "incoming": 3600,
            "prepared": 3600,
            "result": 3600,
        },
        now_ts=now,
        max_scan_files=100,
        max_scan_dirs=100,
    )

    assert not old_incoming.exists()
    assert not old_prepared.exists()
    assert fresh_incoming.exists()
    assert fresh_result.exists()
    assert int(stats["removed_files"]) == 2


def test_cleanup_jobs_artifacts_uses_debug_allowlist_ttl(tmp_path: Path) -> None:
    now = time.time()
    jobs_root = tmp_path / "output" / "jobs"

    debug_keep = jobs_root / "job-a" / "out" / "logs" / "stage2_subtitles.json"
    debug_old = jobs_root / "job-a" / "out" / "logs" / "stage2_subtitles_legacy.json"
    regular_old = jobs_root / "job-a" / "out" / "logs" / "trace.txt"

    _touch(debug_keep, mtime=now - 7200)
    _touch(debug_old, mtime=now - 36000)
    _touch(regular_old, mtime=now - 7200)

    stats = cleanup_jobs_artifacts(
        jobs_roots=[jobs_root],
        regular_retention_s=3600,
        debug_retention_s=21600,
        debug_allowlist_patterns=("stage2_subtitles*.json",),
        now_ts=now,
        max_scan_files=200,
        max_scan_dirs=200,
    )

    assert debug_keep.exists()
    assert not debug_old.exists()
    assert not regular_old.exists()
    assert int(stats["removed_files"]) == 2
    assert int(stats["kept_debug_files"]) >= 1
