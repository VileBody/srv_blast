from __future__ import annotations

from pathlib import Path

import pytest

from web_app.backend.app import db, upload_store


@pytest.fixture()
def upload_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("BLAST_DB_PATH", str(tmp_path / "uploads.sqlite"))
    db.reset_for_tests()
    db.migrate()
    yield
    db.reset_for_tests()


def _metadata(name: str, size: int) -> dict[str, object]:
    return {
        "name": name,
        "url": f"s3://assets/{name}",
        "playbackUrl": f"/api/uploads/{name}",
        "bytes": size,
        "duration": 4.0,
        "width": 1080,
        "height": 1920,
        "hasAudio": True,
    }


def test_project_cleanup_releases_actual_upload_usage(upload_db) -> None:
    user_id = "user-1"
    upload_store.reserve(user_id)
    first = upload_store.save(user_id, "project-a", "source", _metadata("first.mp4", 1_000))
    upload_store.reserve(user_id)
    second = upload_store.save(user_id, "project-a", "source", _metadata("second.mp4", 2_000))
    upload_store.reserve(user_id)
    upload_store.save(user_id, "project-b", "source", _metadata("keep.mp4", 3_000))

    removed = upload_store.remove_project(user_id, "project-a")

    assert {item["id"] for item in removed} == {first["id"], second["id"]}
    assert [item["name"] for item in upload_store.assets(user_id)] == ["keep.mp4"]
    with db.read() as cur:
        cur.execute("SELECT file_count, byte_count FROM web_upload_usage WHERE user_id=?", (user_id,))
        assert cur.fetchone() == (1, 3_000)


def test_failed_phone_upload_restores_reserved_link_slot(upload_db) -> None:
    token, _ = upload_store.make_link("user-1", "project-a", "9:16")

    assert upload_store.link(token, consume=True)["remaining"] == 9
    upload_store.restore_link(token)
    assert upload_store.link(token)["remaining"] == 10


def test_new_phone_link_invalidates_the_previous_one(upload_db) -> None:
    first, _ = upload_store.make_link("user-1", "project-a", "16:9")
    second, _ = upload_store.make_link("user-1", "project-a", "16:9")

    with pytest.raises(ValueError, match="истекла"):
        upload_store.link(first)
    assert upload_store.link(second)["projectId"] == "project-a"
