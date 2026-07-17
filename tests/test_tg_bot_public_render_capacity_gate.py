from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from services.tg_bot_botapi import orchestrator_client as team_client
from services.tg_bot_public import orchestrator_client as public_client


class _FakeAsyncClient:
    async def get(self, url: str) -> httpx.Response:
        assert url == "http://orchestrator/render-capacity"
        return httpx.Response(200, json={"ready": False, "retry_after_seconds": 15})

    async def aclose(self) -> None:
        return None


def test_render_capacity_client_is_mirrored(monkeypatch) -> None:
    for module in (team_client, public_client):
        monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_: _FakeAsyncClient())
        client = module.OrchestratorClient(base_url="http://orchestrator")
        result = asyncio.run(client.get_render_capacity())
        assert result["ready"] is False


def test_bots_gate_before_credit_deduction() -> None:
    root = Path(__file__).resolve().parents[1]
    team = (root / "services/tg_bot_botapi/app.py").read_text(encoding="utf-8")
    public = (root / "services/tg_bot_public/app.py").read_text(encoding="utf-8")

    team_handler = team[team.index("    async def _handle_wait_confirm("):]
    public_handler = public[public.index("    async def _handle_wait_confirm("):]
    assert team_handler.index("get_render_capacity") < team_handler.index("deduct_credit")
    assert public_handler.index("get_render_capacity") < public_handler.index("get_balance")
    assert "Кредит не списан" in team_handler
    assert "Кредит не списан" in public_handler