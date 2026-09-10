from __future__ import annotations

from web_app.backend.app import mock_store as store


def test_legacy_track_gets_stable_server_owned_identity() -> None:
    user_id = "legacy-track-user"
    other_id = "other-track-user"
    store.WORKSPACES.pop(user_id, None)
    store.WORKSPACES.pop(other_id, None)
    try:
        store.use_user(user_id)
        track = store.save_track("old.mp3", s3_url="s3://raw/users/old.mp3")
        assert track["audioHash"] is None

        resolved = store.saved_track(track["id"])
        repeated = store.saved_track(track["id"])
        assert resolved is not None
        assert resolved["audioHash"].startswith("legacy-s3:")
        assert repeated["audioHash"] == resolved["audioHash"]

        store.use_user(other_id)
        assert store.saved_track(track["id"]) is None
    finally:
        store.WORKSPACES.pop(user_id, None)
        store.WORKSPACES.pop(other_id, None)
        store.use_user(store.DEMO_USER_ID)


def test_stale_wizard_client_cannot_erase_explicit_timing() -> None:
    user_id = "legacy-wizard-timing-user"
    store.WORKSPACES.pop(user_id, None)
    try:
        store.use_user(user_id)
        track = {"id": "track-one"}
        store.set_wizard_session({
            "projectId": "project-one",
            "stage": 1,
            "data": {"track": track, "timing": {"from": "01:31:00", "to": "01:48:00"}},
        })

        stale = {"track": track, "timing": {"mode": "ai"}}
        merged = store.preserve_explicit_timing(stale, project_id="project-one")
        assert merged["timing"] == {"from": "01:31:00", "to": "01:48:00"}

        saved = store.set_wizard_session({
            "projectId": "project-one",
            "stage": 5,
            "data": {**stale, "lyrics": "Как сюжеты старой киноленты"},
        })
        assert saved["data"]["timing"] == {"from": "01:31:00", "to": "01:48:00"}
        assert saved["data"]["fragment"] == "Как сюжеты старой киноленты"

        other_track = store.preserve_explicit_timing(
            {"track": {"id": "track-two"}, "timing": {"mode": "ai"}},
            project_id="project-one",
        )
        assert other_track["timing"] == {"mode": "ai"}
    finally:
        store.WORKSPACES.pop(user_id, None)
        store.use_user(store.DEMO_USER_ID)
