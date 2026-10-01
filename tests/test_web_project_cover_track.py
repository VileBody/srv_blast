"""Обложка-дорожка проекта: трек последней генерации, его отрывок и дроп."""
from __future__ import annotations

import pytest

from web_app.backend.app import mock_store as store


@pytest.fixture()
def space():
    user_id = "user_cover_track"
    store.WORKSPACES.pop(user_id, None)
    store.use_user(user_id)
    space = store.ws()
    space.projects = [{"id": "p1", "name": "Ночной город", "startedAt": "2026-09-01T09:00:00+00:00"}]
    yield space
    for job_id in [jid for jid, job in store.JOBS.items() if job.get("userId") == user_id]:
        store.JOBS.pop(job_id)
    store.WORKSPACES.pop(user_id, None)


def _job(space, job_id: str, track_id: str, created: str, *, frm: str = "00:42", to: str = "00:57", drop: str | None = None) -> None:
    hooks = {"dropTime": drop} if drop else {}
    store.JOBS[job_id] = {
        "id": job_id, "projectId": "p1", "userId": space.user["id"], "status": "COMPLETED",
        "stageData": {"track": {"id": track_id}, "timing": {"from": frm, "to": to}, "hooks": hooks},
        "videos": [], "createdAt": created,
    }


def test_cover_follows_latest_job_with_segment_and_drop(space) -> None:
    space.saved_tracks = [
        {"id": "t2", "filename": "new.mp3", "durationS": 180.0},
        {"id": "t1", "filename": "old.mp3", "durationS": 200.0},
    ]
    _job(space, "j1", "t1", "2026-09-01T10:00:00+00:00")
    _job(space, "j2", "t2", "2026-09-05T10:00:00+00:00", frm="01:10", to="01:25", drop="01:18:50")

    cover = store.project_cover_track("p1")

    assert cover == {"trackId": "t2", "filename": "new.mp3", "durationS": 180.0, "from": 70.0, "to": 85.0, "drop": 78.5}


def test_expired_track_is_skipped_for_an_older_one_still_stored(space) -> None:
    space.saved_tracks = [{"id": "t1", "filename": "old.mp3", "durationS": 200.0}]
    _job(space, "j1", "t1", "2026-09-01T10:00:00+00:00")
    _job(space, "j2", "gone", "2026-09-05T10:00:00+00:00")

    assert store.project_cover_track("p1")["trackId"] == "t1"


def test_project_without_generations_has_no_cover_track(space) -> None:
    space.saved_tracks = [{"id": "t1", "filename": "old.mp3", "durationS": 200.0}]

    assert store.project_cover_track("p1") is None
    [project] = store.list_projects()["projects"]
    assert project["coverTrack"] is None
