"""GetCardList parsing — the only after-the-fact source of a lost RebillId."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from services.tg_bot_public.tbank_client import TBankClient


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> Any:
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient and records the posted body."""

    captured: list[tuple[str, dict[str, Any]]] = []
    payload: Any = []
    status_code: int = 200

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    async def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
        type(self).captured.append((url, dict(json)))
        return _FakeResponse(type(self).payload, type(self).status_code)


@pytest.fixture(autouse=True)
def _patch_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAsyncClient.captured = []
    _FakeAsyncClient.payload = []
    _FakeAsyncClient.status_code = 200
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)


def _client() -> TBankClient:
    return TBankClient("term-1", "pass-1")


def test_get_card_list_sends_customer_key_and_token() -> None:
    _FakeAsyncClient.payload = []

    asyncio.run(_client().get_card_list("777"))

    url, body = _FakeAsyncClient.captured[0]
    assert url.endswith("/v2/GetCardList")
    assert body["CustomerKey"] == "777"
    assert body["TerminalKey"] == "term-1"
    assert body["Token"]


def test_find_rebill_id_prefers_active_card_then_newest() -> None:
    _FakeAsyncClient.payload = [
        {"CardId": "10", "Status": "A", "RebillId": "rebill-old"},
        {"CardId": "44", "Status": "I", "RebillId": "rebill-inactive-newer"},
        {"CardId": "31", "Status": "A", "RebillId": "rebill-newest-active"},
    ]

    assert asyncio.run(_client().find_rebill_id("777")) == "rebill-newest-active"


def test_find_rebill_id_ignores_cards_without_rebill() -> None:
    # A card bound outside a Recurrent=Y payment carries no RebillId at all.
    _FakeAsyncClient.payload = [{"CardId": "9", "Status": "A", "RebillId": ""}]

    assert asyncio.run(_client().find_rebill_id("777")) == ""


def test_find_rebill_id_empty_when_no_card_bound() -> None:
    _FakeAsyncClient.payload = []

    assert asyncio.run(_client().find_rebill_id("777")) == ""


def test_get_card_list_treats_error_object_as_no_cards() -> None:
    # Errors come back as an object; success is always a bare array.
    _FakeAsyncClient.payload = {"Success": False, "ErrorCode": "7", "Message": "Нет привязанных карт"}

    assert asyncio.run(_client().get_card_list("777")) == []
    assert asyncio.run(_client().find_rebill_id("777")) == ""


def test_get_card_list_returns_empty_on_http_error() -> None:
    _FakeAsyncClient.status_code = 500
    _FakeAsyncClient.payload = {}

    assert asyncio.run(_client().get_card_list("777")) == []
