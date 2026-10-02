"""Прослойка лёгких копий медиа: превью клипов и трек для прослушки (web_app/backend/app/media_proxy.py)."""
from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_web_asr_preview import _env

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web_app" / "backend"))
from app import media_proxy as mp  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None


# ── подпись ссылок ─────────────────────────────────────────────────────────────

def test_signed_link_round_trips_and_rejects_tampering() -> None:
    token = mp.sign("s" * 40, {"s": "s3://bucket/pins/a.mp4"})
    assert mp.unsign("s" * 40, token) == {"s": "s3://bucket/pins/a.mp4"}
    body, _, mac = token.partition(".")
    forged = mp.sign("s" * 40, {"s": "s3://bucket/other.mp4"}).partition(".")[0] + "." + mac
    with pytest.raises(mp.MediaProxyError):
        mp.unsign("s" * 40, forged)
    with pytest.raises(mp.MediaProxyError):
        mp.unsign("x" * 40, token)


def test_proxy_name_is_stable_and_kind_specific() -> None:
    a = mp.proxy_name("s3://b/k.mp4", "clip")
    assert a == mp.proxy_name("s3://b/k.mp4", "clip")
    assert a != mp.proxy_name("s3://b/k.mp4", "track")
    assert a != mp.proxy_name("s3://b/k2.mp4", "clip")
    assert a.startswith("clip/")


def test_failed_build_is_retried_and_success_is_shared() -> None:
    builder = mp.Builder(workers=1)
    calls = {"n": 0}

    def flaky() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise mp.MediaProxyError("boom")

    with pytest.raises(mp.MediaProxyError):
        builder.run("x", flaky).result(timeout=5)
    builder.run("x", flaky).result(timeout=5)  # упавшая подготовка запускается заново
    builder.run("x", flaky).result(timeout=5)  # удачная — переиспользуется
    assert calls["n"] == 2


# ── настоящее сжатие (там, где есть ffmpeg) ─────────────────────────────────────

def _make_video(path: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(path)], check=True)


def _atoms(path: Path) -> list[str]:
    import struct
    out = []
    with path.open("rb") as fh:
        while True:
            head = fh.read(8)
            if len(head) < 8:
                return out
            size, kind = struct.unpack(">I4s", head)
            out.append(kind.decode("latin1"))
            fh.seek(size - 8, 1)


@pytest.mark.skipif(not HAS_FFMPEG, reason="нужен ffmpeg")
def test_clip_copy_is_small_vertical_and_faststart(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    _make_video(src)
    store = mp.LocalStore(tmp_path / "cache")
    mp.build_clip(store, "clip/aa/x.mp4", lambda dst: dst.write_bytes(src.read_bytes()))
    out = store.path("clip/aa/x.mp4")
    dims = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert dims == "540,960"
    atoms = _atoms(out)
    assert atoms.index("moov") < atoms.index("mdat")  # играть можно с первых байт
    audio = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert audio == ""  # превью кадра без звука — звук играет трек


@pytest.mark.skipif(not HAS_FFMPEG, reason="нужен ffmpeg")
def test_track_copy_and_peaks(tmp_path: Path) -> None:
    src = tmp_path / "t.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=3", str(src)], check=True)
    store = mp.LocalStore(tmp_path / "cache")
    mp.build_track(store, "track/aa/t", lambda dst: dst.write_bytes(src.read_bytes()))
    peaks = json.loads(store.read("track/aa/t.json"))
    assert peaks["rate"] == 20 and abs(peaks["duration"] - 3) < 0.05
    assert len(peaks["rms"]) == 60 and all(0 < v < 1 for v in peaks["rms"])
    assert store.has("track/aa/t.m4a")


# ── эндпоинты (ffmpeg подменён — проверяем прослойку, а не кодек) ─────────────────

@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _env(monkeypatch)
    saved = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    for name in saved:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "MEDIA_LOCAL", tmp_path / "cache")
    calls = {"transcode": 0}

    def fake_transcode(src: Path, dst: Path, args: list[str]) -> None:
        calls["transcode"] += 1
        dst.write_bytes(b"FAKEMEDIA" * 200)

    monkeypatch.setattr(main.media_proxy, "transcode", fake_transcode)
    monkeypatch.setattr(main.media_proxy, "peaks", lambda src: {"rate": 20, "duration": 1.0, "rms": [0.1] * 20})
    from fastapi.testclient import TestClient

    with TestClient(main.app) as tc:
        tc.post("/api/dev/login")
        yield tc, main, calls
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        sys.modules.pop(name, None)
    sys.modules.update(saved)


def test_track_media_is_built_once_and_supports_range(client) -> None:
    tc, main, calls = client
    track = main.store.ws().saved_tracks[0]
    full = tc.get(f"/api/wizard/media/track/{track['id']}")
    assert full.status_code == 200 and full.content.startswith(b"FAKEMEDIA")
    part = tc.get(f"/api/wizard/media/track/{track['id']}", headers={"Range": "bytes=0-8"})
    assert part.status_code == 206 and part.content == b"FAKEMEDIA"
    peaks = tc.get(f"/api/wizard/media/track/{track['id']}/peaks").json()
    assert peaks["rate"] == 20 and len(peaks["rms"]) == 20
    assert calls["transcode"] == 1  # второй и третий запрос — из готовой копии


def test_track_media_only_for_own_tracks(client) -> None:
    tc, _, _ = client
    assert tc.get("/api/wizard/media/track/someone-else").status_code == 404
    assert tc.get("/api/wizard/media/track/someone-else/peaks").status_code == 404


def test_clip_link_must_be_signed(client) -> None:
    tc, _, _ = client
    assert tc.get("/api/wizard/media/clip/not-a-token/clip.mp4").status_code == 404
    assert tc.get("/api/wizard/media/clip/not-a-token/poster.jpg?t=1").status_code == 404


def test_prewarm_is_a_no_op_in_mock(client) -> None:
    tc, _, _ = client
    r = tc.post("/api/wizard/media/prewarm", json={"group": "Ночной город", "clipFrom": "00:10:00", "clipTo": "00:22:00"})
    assert r.status_code == 200 and r.json()["queued"] == 0


def test_signed_clip_and_its_poster_are_served(client, monkeypatch) -> None:
    tc, main, calls = client
    monkeypatch.setattr(main.media_proxy, "transcode_frame", lambda src, dst, at: dst.write_bytes(b"\xff\xd8JPEG"))
    track = main.store.ws().saved_tracks[0]
    token = main.media_proxy.sign(main.RUNTIME.session_secret, {"s": str(track["localUrl"])})
    clip = tc.get(f"/api/wizard/media/clip/{token}/clip.mp4")
    assert clip.status_code == 200 and clip.content.startswith(b"FAKEMEDIA")
    poster = tc.get(f"/api/wizard/media/clip/{token}/poster.jpg?t=1.24")
    assert poster.status_code == 200 and poster.content.startswith(b"\xff\xd8")
    assert calls["transcode"] == 1  # кадр снят с уже готовой лёгкой копии, не с оригинала


def test_clip_link_looks_like_video_to_the_site() -> None:
    # сайт отличает видео от картинки по расширению (lib/media.ts isVideoUrl)
    import re
    url = "/api/wizard/media/clip/abc.def/clip.mp4"
    assert re.search(r"\.(mp4|webm|mov|m4v)(?:[?#]|$)", url, re.I)
