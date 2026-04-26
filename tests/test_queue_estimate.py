from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.queue_estimate import (
    build_queue_estimate,
    format_queue_estimate_lines,
    pick_queue_estimate_job_id,
)


def _job(
    job_id: str,
    *,
    status: str,
    created_at: float,
    started_at: float | None = None,
    finished_at: float | None = None,
):
    return SimpleNamespace(
        job_id=job_id,
        status=status,
        created_at=created_at,
        updated_at=finished_at or started_at or created_at,
        queued_at=created_at,
        started_at=started_at,
        finished_at=finished_at,
    )


def test_build_queue_estimate_uses_recent_successful_moving_average() -> None:
    jobs = [
        _job("done-old", status="SUCCEEDED", created_at=0.0, finished_at=60.0),
        _job("done-2", status="SUCCEEDED", created_at=100.0, finished_at=220.0),
        _job("done-1", status="SUCCEEDED", created_at=200.0, finished_at=380.0),
        _job("active-running", status="RUNNING", created_at=400.0, started_at=420.0),
        _job("target", status="QUEUED", created_at=430.0),
    ]

    estimate = build_queue_estimate(jobs, job_id="target", window_size=2, now=450.0)

    assert estimate is not None
    assert estimate["active"] is True
    assert estimate["queue_position"] == 2
    assert estimate["active_jobs_total"] == 2
    assert estimate["sample_size"] == 2
    assert estimate["avg_duration_seconds"] == pytest.approx(150.0)
    assert estimate["eta_seconds"] == pytest.approx(270.0)


def test_format_queue_estimate_lines_is_user_friendly() -> None:
    lines = format_queue_estimate_lines(
        {
            "active": True,
            "queue_position": 3,
            "active_jobs_total": 8,
            "eta_seconds": 61,
            "sample_size": 50,
        }
    )

    assert lines == ["Очередь: #3 из 8", "Ожидание: примерно ~2 мин"]


def test_pick_queue_estimate_job_id_uses_first_non_terminal_row() -> None:
    assert (
        pick_queue_estimate_job_id(
            [
                {"job_id": "done", "status": "SUCCEEDED"},
                {"job_id": "active", "status": "RUNNING"},
                {"job_id": "queued", "status": "QUEUED"},
            ]
        )
        == "active"
    )
