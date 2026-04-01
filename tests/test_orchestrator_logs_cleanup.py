from __future__ import annotations

import os
from pathlib import Path

from services.orchestrator import tasks


def _touch_with_mtime(path: Path, *, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_cleanup_old_job_logs_keeps_current_and_recent(monkeypatch, tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    now = 10_000.0

    old_other = output_dir / "jobs" / "job_old" / "out" / "logs" / "old.log"
    new_other = output_dir / "jobs" / "job_old" / "out" / "logs" / "new.log"
    old_current = output_dir / "jobs" / "job_current" / "out" / "logs" / "old_current.log"

    _touch_with_mtime(old_other, mtime=now - 7200.0)
    _touch_with_mtime(new_other, mtime=now - 120.0)
    _touch_with_mtime(old_current, mtime=now - 7200.0)

    monkeypatch.setenv("JOB_LOG_RETENTION_SECONDS", "3600")

    summary = tasks._cleanup_old_job_logs(
        output_dir=str(output_dir),
        current_job_id="job_current",
        now_ts=now,
    )

    assert summary["ttl_s"] == 3600
    assert summary["deleted_files"] == 1
    assert summary["scanned_job_logs_dirs"] == 1
    assert summary["skipped_current_job"] == 1

    assert not old_other.exists()
    assert new_other.exists()
    assert old_current.exists()
