# services/orchestrator/cleanup.py
"""
Job artifact cleanup policy.

Removes local job working directories (work/ and output/) that are older
than the configured retention period. Intended to be called periodically
(e.g., from a cron task or a Celery beat schedule).

Usage:
    python -m services.orchestrator.cleanup
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from .config import SETTINGS

log = logging.getLogger(__name__)


def cleanup_old_job_artifacts(
    *,
    work_dir: str = "",
    output_dir: str = "",
    max_age_h: int = 0,
    dry_run: bool = False,
) -> dict:
    """
    Remove job directories older than max_age_h hours.
    Returns summary dict with counts.
    """
    w = Path(work_dir or SETTINGS.work_dir).resolve() / "jobs"
    o = Path(output_dir or SETTINGS.output_dir).resolve() / "jobs"
    age_h = max_age_h or SETTINGS.job_artifact_max_age_h
    cutoff = time.time() - (age_h * 3600)

    removed = 0
    skipped = 0
    errors = 0

    for root in (w, o):
        if not root.exists():
            continue
        for job_dir in root.iterdir():
            if not job_dir.is_dir():
                continue
            try:
                mtime = job_dir.stat().st_mtime
                if mtime >= cutoff:
                    skipped += 1
                    continue
                if dry_run:
                    log.info("cleanup_dry_run: would remove %s (age_h=%.1f)", job_dir, (time.time() - mtime) / 3600)
                    removed += 1
                    continue
                shutil.rmtree(job_dir, ignore_errors=True)
                removed += 1
                log.info("cleanup_removed: %s", job_dir)
            except Exception as e:
                errors += 1
                log.warning("cleanup_error: %s err=%r", job_dir, e)

    return {"removed": removed, "skipped": skipped, "errors": errors, "max_age_h": age_h}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if not SETTINGS.job_artifact_cleanup_enabled:
        log.info("cleanup disabled (JOB_ARTIFACT_CLEANUP_ENABLED=0)")
    else:
        result = cleanup_old_job_artifacts()
        log.info("cleanup done: %s", result)
