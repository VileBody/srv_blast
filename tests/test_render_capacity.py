from __future__ import annotations

import json

from services.orchestrator import render_capacity


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self._body


class _Opener:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses

    def open(self, request, timeout: float):
        assert timeout > 0
        value = self.responses[request.full_url]
        if isinstance(value, Exception):
            raise value
        return value


def test_probe_render_capacity_accepts_one_healthy_node(monkeypatch) -> None:
    opener = _Opener({
        "http://dead/health": OSError("offline"),
        "http://ready/health": _Response({"status": "ok"}),
    })
    monkeypatch.setattr(render_capacity.urllib.request, "build_opener", lambda *_: opener)

    result = render_capacity.probe_render_capacity(["http://dead/", "http://ready"])

    assert result["ready"] is True
    assert result["healthy_urls"] == ["http://ready"]
    assert result["configured_count"] == 2


def test_probe_render_capacity_fails_closed(monkeypatch) -> None:
    opener = _Opener({"http://node/health": _Response({"ready": False})})
    monkeypatch.setattr(render_capacity.urllib.request, "build_opener", lambda *_: opener)

    result = render_capacity.probe_render_capacity(["http://node"])

    assert result["ready"] is False
    assert result["reason"] == "no_healthy_render_nodes"
    assert result["healthy_urls"] == []