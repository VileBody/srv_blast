from __future__ import annotations

import sys
import types
from types import SimpleNamespace

if "asyncpg" not in sys.modules:
    asyncpg_stub = types.ModuleType("asyncpg")

    class _DummyPool:
        async def close(self):
            return None

    class _DummyConnection:
        pass

    async def _create_pool(*args, **kwargs):
        _ = (args, kwargs)
        return _DummyPool()

    asyncpg_stub.Pool = _DummyPool
    asyncpg_stub.Connection = _DummyConnection
    asyncpg_stub.create_pool = _create_pool
    sys.modules["asyncpg"] = asyncpg_stub

from services.orchestrator import app as orchestrator_app


def test_maintenance_bypass_allowed_when_token_matches(monkeypatch) -> None:
    monkeypatch.setattr(
        orchestrator_app,
        "SETTINGS",
        SimpleNamespace(system_maintenance_bypass_token="shared-token"),
        raising=False,
    )
    req = SimpleNamespace(maintenance_bypass_token="shared-token")
    assert orchestrator_app._maintenance_bypass_allowed(req) is True


def test_maintenance_bypass_rejected_when_token_missing_or_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        orchestrator_app,
        "SETTINGS",
        SimpleNamespace(system_maintenance_bypass_token="shared-token"),
        raising=False,
    )

    assert orchestrator_app._maintenance_bypass_allowed(SimpleNamespace(maintenance_bypass_token="")) is False
    assert orchestrator_app._maintenance_bypass_allowed(SimpleNamespace(maintenance_bypass_token="wrong")) is False
    assert orchestrator_app._maintenance_bypass_allowed(None) is False
