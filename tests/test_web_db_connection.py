from __future__ import annotations

from typing import Any

import pytest

from web_app.backend.app import db


class _Cursor:
    def __init__(self, owner: "_Connection") -> None:
        self.owner = owner

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, query: str) -> None:
        self.owner.queries.append(query)
        if self.owner.broken:
            raise RuntimeError("server closed the idle session")


class _Connection:
    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken
        self.closed = False
        self.queries: list[str] = []
        self.commits = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def clean_connection() -> None:
    db.reset_for_tests()
    yield
    db.reset_for_tests()


def test_postgres_connection_is_health_checked_after_interval(
    monkeypatch: pytest.MonkeyPatch, clean_connection: None
) -> None:
    live = _Connection()
    monkeypatch.setattr(db, "dialect", lambda: "postgres")
    monkeypatch.setattr(db, "_new_connection", lambda: live)
    db._local.conn = live
    db._local.db_health_checked_at = 0.0

    assert db._connection() is live
    assert live.queries == ["SELECT 1"]
    assert live.commits == 1


def test_postgres_idle_session_is_replaced_once(
    monkeypatch: pytest.MonkeyPatch, clean_connection: None
) -> None:
    stale = _Connection(broken=True)
    replacement = _Connection()
    monkeypatch.setattr(db, "dialect", lambda: "postgres")
    monkeypatch.setattr(db, "_new_connection", lambda: replacement)
    db._local.conn = stale
    db._local.db_health_checked_at = 0.0

    assert db._connection() is replacement
    assert stale.closed is True
    assert replacement.queries == []
