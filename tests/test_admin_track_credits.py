from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.tg_bot_public.admin_panel import build_app


class _CreditsDB:
    def __init__(self) -> None:
        self.video_calls: list[tuple] = []
        self.track_calls: list[tuple] = []
        self.audit_calls: list[tuple] = []

    async def add_credits(self, *args, **kwargs) -> None:
        self.video_calls.append((args, kwargs))

    async def add_track_credits(self, *args, **kwargs) -> None:
        self.track_calls.append((args, kwargs))

    async def audit_log(self, *args, **kwargs) -> None:
        self.audit_calls.append((args, kwargs))


class _StateStore:
    redis = object()


def _client(db: _CreditsDB) -> TestClient:
    settings = SimpleNamespace(
        admin_panel_password="secret",
        orchestrator_public_url="",
        windows_donor_url="",
        admin_panel_enable_donor_restart=False,
        twc_token="",
        season_redis_prefix="season:",
    )
    return TestClient(build_app(db, _StateStore(), settings))  # type: ignore[arg-type]


def test_admin_can_add_track_credits_without_touching_video_balance() -> None:
    db = _CreditsDB()
    with _client(db) as client:
        response = client.post(
            "/admin/users/777/credits",
            auth=("admin", "secret"),
            data={
                "credit_kind": "track",
                "amount": "2",
                "reason": "support",
                "order_id": "order-42",
                "note": "manual correction",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/users/777"
    assert db.video_calls == []
    assert db.track_calls == [
        (
            (777, 2, "support"),
            {"admin_note": "manual correction | via panel by admin | order order-42"},
        )
    ]
    assert db.audit_calls == [
        (("admin", "user_track_credits", "777", "amount=2 reason=support order_id=order-42"), {})
    ]


def test_admin_video_credit_path_remains_separate() -> None:
    db = _CreditsDB()
    with _client(db) as client:
        response = client.post(
            "/admin/users/777/credits",
            auth=("admin", "secret"),
            data={"credit_kind": "video", "amount": "3", "reason": "support"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert db.track_calls == []
    assert db.video_calls == [
        (
            (777, 3, "support"),
            {"admin_note": "via panel by admin", "actor": "admin", "order_id": ""},
        )
    ]


def test_admin_rejects_unknown_credit_kind() -> None:
    db = _CreditsDB()
    with _client(db) as client:
        response = client.post(
            "/admin/users/777/credits",
            auth=("admin", "secret"),
            data={"credit_kind": "other", "amount": "1"},
            follow_redirects=False,
        )

    assert response.status_code == 422
    assert db.video_calls == []
    assert db.track_calls == []
