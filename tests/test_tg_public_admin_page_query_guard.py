from __future__ import annotations

import base64
from dataclasses import replace

from fastapi.testclient import TestClient

from services.tg_bot_public.admin_panel import build_app
from services.tg_bot_public.config import SETTINGS


class _FakeCreditsDB:
    async def list_users(self, *, limit: int, offset: int):  # noqa: ARG002
        return []

    async def count_users(self) -> int:
        return 0

    async def get_activity(self, *, limit: int, offset: int = 0, tg_id: int | None = None):  # noqa: ARG002
        return []

    async def count_activity(self) -> int:
        return 0

    async def get_transactions(self, *, limit: int, offset: int = 0, tg_id: int | None = None):  # noqa: ARG002
        return []

    async def count_transactions(self) -> int:
        return 0

    async def get_payments(self, *, limit: int, offset: int = 0):  # noqa: ARG002
        return []

    async def count_payments(self) -> int:
        return 0


class _FakeStateStore:
    async def list_all_states(self):
        return []


def _auth_headers(password: str) -> dict[str, str]:
    token = base64.b64encode(f"admin:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_admin_list_pages_ignore_invalid_page_query() -> None:
    password = "pw"
    settings = replace(SETTINGS, admin_panel_password=password)
    app = build_app(_FakeCreditsDB(), _FakeStateStore(), settings)
    client = TestClient(app)
    headers = _auth_headers(password)

    for path in [
        "/admin/users?page=abc",
        "/admin/activity?page=abc",
        "/admin/transactions?page=abc",
        "/admin/payments?page=abc",
    ]:
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200
