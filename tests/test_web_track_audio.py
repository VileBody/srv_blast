"""Байты трека для волны — со своего домена (presigned S3 + fetch упирается в CORS бакета)."""
from __future__ import annotations

import importlib
import io
import sys

import pytest

from tests.test_web_asr_preview import _env


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch)
    saved = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    for name in saved:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    from fastapi.testclient import TestClient

    with TestClient(main.app) as tc:
        yield tc, main
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)
    sys.modules.update(saved)


def test_mock_redirects_to_the_same_origin_file(client) -> None:
    tc, main = client
    track = main.store.ws().saved_tracks[0]
    r = tc.get(f"/api/wizard/track-audio?trackId={track['id']}", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].startswith("/static/")


def test_only_own_tracks(client) -> None:
    tc, _ = client
    assert tc.get("/api/wizard/track-audio?trackId=someone-else").status_code == 404


def test_production_streams_the_s3_object(client, monkeypatch: pytest.MonkeyPatch) -> None:
    tc, main = client
    track = main.store.ws().saved_tracks[0]
    track["s3Key"] = "s3://raw-audio/web/u1/track.mp3"

    class _Backend:
        def open_track_audio(self, value):
            assert value == "s3://raw-audio/web/u1/track.mp3"
            return _Body(b"ID3-audio-bytes"), "audio/mpeg", 15

    class _Body(io.BytesIO):
        def iter_chunks(self, chunk_size=1024):
            while chunk := self.read(chunk_size):
                yield chunk

    import dataclasses
    monkeypatch.setattr(main, "RUNTIME", dataclasses.replace(main.RUNTIME, backend="production"))
    monkeypatch.setattr(main, "_production_backend", lambda: _Backend())
    r = tc.get(f"/api/wizard/track-audio?trackId={track['id']}")
    assert r.status_code == 200 and r.content == b"ID3-audio-bytes"
    assert r.headers["content-type"] == "audio/mpeg" and r.headers["content-length"] == "15"
