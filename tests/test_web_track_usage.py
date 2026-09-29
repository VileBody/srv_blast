"""«Куда ушли треки»: список в профиле сверяется со счётчиком лимита треков."""
from __future__ import annotations

import pytest

from web_app.backend.app import mock_store as store


@pytest.fixture()
def space():
    user_id = "user_track_usage"
    store.WORKSPACES.pop(user_id, None)
    store.use_user(user_id)
    space = store.ws()
    space.projects = [{"id": "p1", "name": "Ночной город"}]
    yield space
    for job_id in [jid for jid, job in store.JOBS.items() if job.get("userId") == user_id]:
        store.JOBS.pop(job_id)
    store.WORKSPACES.pop(user_id, None)


def _track(track_id: str, name: str, audio_hash: str, created: str) -> dict:
    return {"id": track_id, "filename": name, "audioHash": audio_hash, "s3Key": f"s3://t/{track_id}", "createdAt": created}


def _job(space, job_id: str, track: dict, frm: str, to: str, statuses: list[str], created: str) -> None:
    store.JOBS[job_id] = {
        "id": job_id, "projectId": "p1", "userId": space.user["id"], "status": "COMPLETED",
        "stageData": {"track": {"id": track["id"], "audioHash": track["audioHash"]}, "timing": {"from": frm, "to": to}},
        "videos": [{"status": status} for status in statuses], "createdAt": created,
    }


def test_same_track_uploaded_twice_is_one_entry_dated_by_first_upload(space) -> None:
    first = _track("t1", "song.mp3", "h1", "2026-09-01T10:00:00+00:00")
    again = _track("t2", "song (copy).mp3", "h1", "2026-09-05T10:00:00+00:00")
    space.saved_tracks = [again, first]  # стор хранит новыми вперёд
    _job(space, "j1", first, "00:45", "01:05", ["COMPLETED", "COMPLETED", "FAILED"], "2026-09-02T10:00:00+00:00")
    _job(space, "j2", again, "01:31", "01:48", ["COMPLETED"], "2026-09-06T10:00:00+00:00")

    [entry] = store.track_usage()

    assert entry["name"] == "song.mp3"
    assert entry["spentAt"] == "2026-09-01T10:00:00+00:00"
    assert [f["jobId"] for f in entry["fragments"]] == ["j2", "j1"]
    expected = {"from": 45.0, "to": 65.0, "videos": 2, "videosFailed": 1, "projectName": "Ночной город"}
    assert {key: entry["fragments"][1][key] for key in expected} == expected


def test_production_list_follows_spent_rows_and_names_bot_tracks(space) -> None:
    web = _track("t1", "song.mp3", "h1", "2026-09-01T10:00:00+00:00")
    legacy = _track("t9", "not-charged.mp3", "h9", "2026-08-01T10:00:00+00:00")
    space.saved_tracks = [web, legacy]
    spent = [
        {"audio_hash": "hbot", "created_at": "2026-08-20T09:00:00+00:00"},
        {"audio_hash": "h1", "created_at": "2026-09-01T10:00:01+00:00"},
    ]

    entries = store.track_usage(spent)

    assert [(e["source"], e["name"]) for e in entries] == [("web", "song.mp3"), ("bot", None)]
    assert entries[0]["spentAt"] == "2026-09-01T10:00:01+00:00"
    assert entries[1]["fragments"] == []
