"""Periodic cleanup for per-chat tmp directories (incoming / prepared / result)."""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)


def cleanup_old_tmp_dirs(
    tmp_dir: Path,
    *,
    max_age_h: float = 48.0,
    dry_run: bool = False,
) -> dict:
    """Remove per-chat tmp sub-directories older than *max_age_h* hours.

    Each chat gets ``tmp_dir/<chat_id>/{incoming,prepared,result}``.
    We remove the entire ``<chat_id>`` directory if all contents are older
    than the threshold.

    Returns a summary dict with counts.
    """
    cutoff = time.time() - (max_age_h * 3600)
    removed = 0
    skipped = 0
    errors = 0

    if not tmp_dir.exists():
        return {"removed": 0, "skipped": 0, "errors": 0, "max_age_h": max_age_h}

    for chat_dir in tmp_dir.iterdir():
        if not chat_dir.is_dir():
            continue
        try:
            # Use the most recent mtime among all files inside.
            newest = _newest_mtime(chat_dir)
            if newest >= cutoff:
                skipped += 1
                continue
            if dry_run:
                log.info("tmp_cleanup_dry_run: would remove %s (age_h=%.1f)",
                         chat_dir, (time.time() - newest) / 3600)
                removed += 1
                continue
            shutil.rmtree(chat_dir, ignore_errors=True)
            removed += 1
            log.info("tmp_cleanup_removed: %s", chat_dir)
        except Exception as e:
            errors += 1
            log.warning("tmp_cleanup_error: %s err=%r", chat_dir, e)

    return {"removed": removed, "skipped": skipped, "errors": errors, "max_age_h": max_age_h}


def _newest_mtime(d: Path) -> float:
    """Return the newest mtime inside a directory tree, or the dir's own mtime."""
    newest = d.stat().st_mtime
    for p in d.rglob("*"):
        try:
            mt = p.stat().st_mtime
            if mt > newest:
                newest = mt
        except OSError:
            pass
    return newest
