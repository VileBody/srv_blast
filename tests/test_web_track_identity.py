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
