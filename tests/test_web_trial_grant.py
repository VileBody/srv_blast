from __future__ import annotations

import asyncio
import os
from typing import Any

os.environ["MODE"] = "dev"
os.environ["BLAST_BACKEND_MODE"] = "mock"
os.environ["APP_URL"] = "http://localhost:5173"
os.environ["BLAST_SESSION_SECRET"] = "test-session-secret"
os.environ["BLAST_CORS_ORIGINS"] = "http://localhost:5173"

from services.tg_bot_public.credits_db import CreditsDB
from web_app.backend.app.billing_backend import (
    BillingBackend,
    TRIAL_TRACK_CREDITS,
    TRIAL_VIDEO_CREDITS,
)


class _Tx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _Conn:
    def __init__(self) -> None:
        self.users: dict[int, dict[str, int]] = {}
        self.initial_grants: set[int] = set()

    def transaction(self) -> _Tx:
        return _Tx()

    async def execute(self, query: str, *args: Any) -> str:
        if query.startswith("INSERT INTO users"):
            self.users.setdefault(int(args[0]), {"credits": 0, "track_credits": 0})
            return "INSERT 0 1"
        if query.startswith("INSERT INTO transactions"):
            self.initial_grants.add(int(args[0]))
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {query}")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, int]:
        if query.startswith("SELECT credits, track_credits FROM users"):
            return dict(self.users[int(args[0])])
        if query.startswith("UPDATE users SET credits = credits + $1"):
            videos, tracks, tg_id = int(args[0]), int(args[1]), int(args[2])
            self.users[tg_id]["credits"] += videos
            self.users[tg_id]["track_credits"] += tracks
            return dict(self.users[tg_id])
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def fetchval(self, query: str, *args: Any) -> int | None:
        if "reason = 'initial_grant'" in query:
            return 1 if int(args[0]) in self.initial_grants else None
        raise AssertionError(f"unexpected fetchval: {query}")


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Conn:
        return self.conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


def test_shared_trial_grant_is_idempotent() -> None:
    conn = _Conn()
    db = CreditsDB("postgresql://example")
    db._pool = _Pool(conn)

    first = asyncio.run(db.grant_initial_credits_once(777, 5, 1, actor="test"))
    replay = asyncio.run(db.grant_initial_credits_once(777, 5, 1, actor="test"))

    assert first == {"applied": True, "credits": 5, "track_credits": 1}
    assert replay == {"applied": False, "credits": 5, "track_credits": 1}
    assert conn.users[777] == {"credits": 5, "track_credits": 1}


class _BillingDB:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def ensure_user(self, tg_id: int, username: str) -> None:
        self.calls.append(("ensure", tg_id, username))

    async def grant_initial_credits_once(
        self, tg_id: int, credits: int, track_credits: int, *, actor: str
    ) -> dict[str, Any]:
        self.calls.append(("grant", tg_id, credits, track_credits, actor))
        return {"applied": True}


def test_web_user_receives_the_advertised_trial() -> None:
    db = _BillingDB()
    backend = BillingBackend.__new__(BillingBackend)
    backend._db = db

    asyncio.run(backend.ensure_user(777, "furori63"))

    assert db.calls == [
        ("ensure", 777, "furori63"),
        ("grant", 777, TRIAL_VIDEO_CREDITS, TRIAL_TRACK_CREDITS, "blast_web"),
    ]
