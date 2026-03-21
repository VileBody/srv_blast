from __future__ import annotations

from typing import Any, List

import pytest
from pydantic import BaseModel

from mlcore.gemini_client import GeminiClient, GeminiSettings


class _OutModel(BaseModel):
    ok: int


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, *, calls: List[str], behavior: List[Any]) -> None:
        self._calls = calls
        self._behavior = behavior

    def generate_content(self, *, model: str, contents: List[object], config: Any) -> _FakeResponse:
        del contents, config
        self._calls.append(str(model))
        if not self._behavior:
            raise RuntimeError("unexpected generate_content call")
        action = self._behavior.pop(0)
        if isinstance(action, BaseException):
            raise action
        return _FakeResponse(str(action))


class _FakeClient:
    def __init__(self, *, calls: List[str], behavior: List[Any]) -> None:
        self.models = _FakeModels(calls=calls, behavior=behavior)


def test_gemini_client_uses_fallback_model_on_transient_503(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[str] = []
    clients = [
        _FakeClient(calls=calls, behavior=[RuntimeError("503 UNAVAILABLE: high demand")]),
        _FakeClient(calls=calls, behavior=['{"ok": 1}']),
    ]

    def _fake_make_client(*, api_key: str, proxy: str, timeout_s: float) -> _FakeClient:
        del api_key, proxy, timeout_s
        if not clients:
            raise RuntimeError("unexpected make_client call")
        return clients.pop(0)

    monkeypatch.setattr("mlcore.gemini_client.make_client", _fake_make_client)

    client = GeminiClient(
        GeminiSettings(
            api_key="k",
            model="gemini-2.5-pro",
            fallback_model="gemini-3-flash-preview",
        )
    )
    out = client.generate_structured(schema_model=_OutModel, prompt="p")

    assert isinstance(out, _OutModel)
    assert int(out.ok) == 1
    assert calls == ["gemini-2.5-pro", "gemini-3-flash-preview"]


def test_gemini_client_does_not_use_fallback_for_non_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: List[str] = []
    clients = [
        _FakeClient(calls=calls, behavior=[RuntimeError("400 BAD_REQUEST")]),
        _FakeClient(calls=calls, behavior=['{"ok": 1}']),
    ]

    def _fake_make_client(*, api_key: str, proxy: str, timeout_s: float) -> _FakeClient:
        del api_key, proxy, timeout_s
        if not clients:
            raise RuntimeError("unexpected make_client call")
        return clients.pop(0)

    monkeypatch.setattr("mlcore.gemini_client.make_client", _fake_make_client)

    client = GeminiClient(
        GeminiSettings(
            api_key="k",
            model="gemini-2.5-pro",
            fallback_model="gemini-3-flash-preview",
        )
    )

    with pytest.raises(RuntimeError, match="400 BAD_REQUEST"):
        client.generate_structured(schema_model=_OutModel, prompt="p")

    assert calls == ["gemini-2.5-pro"]
