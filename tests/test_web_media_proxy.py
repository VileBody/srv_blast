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


@pytest.fixture(autouse=True)
def _fresh_job_registry():
    # реестр работ общий на процесс: у каждого теста своё хранилище, готовые работы не переносим
    mp._JOBS.clear()
    mp._OWNER.clear()
    yield
    mp._JOBS.clear()
    mp._OWNER.clear()


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


JPEG = bytes([0xFF, 0xD8]) + b"JPEG"


def test_poster_without_clip_reads_the_original_directly(client, monkeypatch) -> None:
    """Прогрев до клипа не дошёл — кадр снимается с оригинала, клип целиком не сжимается."""
    tc, main, calls = client
    seen = []
    monkeypatch.setattr(main.media_proxy, "transcode_frame", lambda src, dst, at: (seen.append((src, at)), dst.write_bytes(JPEG)))
    track = main.store.ws().saved_tracks[0]
    token = main.media_proxy.sign(main.RUNTIME.session_secret, {"s": str(track["localUrl"])})
    poster = tc.get(f"/api/wizard/media/clip/{token}/poster.jpg?t=0.5")
    assert poster.status_code == 200
    assert calls["transcode"] == 0 and len(seen) == 1 and seen[0][1] == 0.5
    assert not seen[0][0].endswith("clip.mp4")  # вход — оригинал, не временная копия


def test_urgent_pool_takes_over_a_queued_background_job() -> None:
    """Экран просит файл, который ещё стоит в очереди прогрева, — не ждём очередь."""
    import threading
    gate = threading.Event()
    background = mp.Builder(workers=1)
    urgent = mp.Builder(workers=1, urgent=True)
    background.run("busy", lambda: gate.wait(5))  # единственный фоновый воркер занят
    queued = background.run("wanted", lambda: None)
    done = urgent.run("wanted", lambda: None)
    done.result(timeout=2)  # выполнено срочным пулом, пока фоновый ещё занят
    assert queued.cancelled()
    assert urgent.run("wanted", lambda: None) is done
    gate.set()


def test_same_file_is_not_built_twice_across_pools() -> None:
    import threading
    gate = threading.Event()
    calls = {"n": 0}

    def slow() -> None:
        calls["n"] += 1
        gate.wait(5)

    a = mp.Builder(workers=1).run("clip", slow)
    while not a.running():
        pass
    b = mp.Builder(workers=1, urgent=True).run("clip", slow)  # уже идёт — ждём её, а не дублируем
    gate.set()
    b.result(timeout=5)
    assert a is b and calls["n"] == 1


def test_urgent_pool_keeps_its_own_queued_job() -> None:
    """Второй запрос того же клипа, пока он стоит в срочной очереди, ждёт ту же работу.

    Раньше он отменял её и ставил заново в конец очереди: первый запрос падал (CancelledError →
    503, <video> оставался на обложке), а каждый новый запрос отодвигал сжатие ещё дальше.
    """
    import threading
    gate = threading.Event()
    urgent = mp.Builder(workers=1, urgent=True)
    urgent.run("busy", lambda: gate.wait(5))  # единственный срочный воркер занят
    first = urgent.run("wanted", lambda: None)
    again = urgent.run("wanted", lambda: None)
    assert again is first and not first.cancelled()
    other_urgent = mp.Builder(workers=1, urgent=True).run("wanted", lambda: None)
    assert other_urgent is first  # чужой срочный пул тоже не перехватывает — ждёт
    gate.set()
    first.result(timeout=5)


def _queued_clip(main, monkeypatch):
    """Срочный пул занят — клип встаёт в очередь. Возвращает (адрес клипа, «отпустить пул»)."""
    import threading
    gate = threading.Event()
    now = main.media_proxy.Builder(workers=1, urgent=True)
    monkeypatch.setattr(main.media_proxy, "NOW", now)
    now.run("busy", lambda: gate.wait(10))
    track = main.store.ws().saved_tracks[0]
    token = main.media_proxy.sign(main.RUNTIME.session_secret, {"s": str(track["localUrl"])})
    return f"/api/wizard/media/clip/{token}/clip.mp4", gate


def _get_in_threads(tc, url: str, n: int):
    import threading
    results: list = [None] * n

    def go(i: int) -> None:
        results[i] = tc.get(url)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    return threads, results


def test_parallel_requests_for_a_queued_clip_all_get_it(client, monkeypatch) -> None:
    """Тот же клип просят сразу несколько <video> (превью стола, сетка, плитки) — все получают его."""
    import time
    tc, main, calls = client
    url, gate = _queued_clip(main, monkeypatch)
    threads, results = _get_in_threads(tc, url, 3)
    time.sleep(0.5)  # все три запроса ждут в очереди срочного пула
    gate.set()
    for th in threads:
        th.join(timeout=15)
    assert [r.status_code for r in results] == [200, 200, 200]
    assert all(r.content.startswith(b"FAKEMEDIA") for r in results)
    assert calls["transcode"] == 1


def test_waiting_for_a_clip_does_not_hold_request_threads(client, monkeypatch) -> None:
    """Пока клипы сжимаются, остальной сайт отвечает: ожидание идёт в event loop, не в потоках."""
    import time
    import anyio.to_thread
    tc, main, _ = client
    limiter = tc.portal.call(anyio.to_thread.current_default_thread_limiter)
    tokens = limiter.total_tokens
    tc.portal.call(setattr, limiter, "total_tokens", 2)
    try:
        url, gate = _queued_clip(main, monkeypatch)
        threads, results = _get_in_threads(tc, url, 4)  # ждущих клипов больше, чем потоков
        time.sleep(0.5)
        started = time.monotonic()
        other = tc.get("/api/auth/providers")  # обычный синхронный обработчик
        assert other.status_code == 200 and time.monotonic() - started < 5
        gate.set()
        for th in threads:
            th.join(timeout=15)
        assert [r.status_code for r in results] == [200] * 4
    finally:
        tc.portal.call(setattr, limiter, "total_tokens", tokens)


def test_clip_link_looks_like_video_to_the_site() -> None:
    # сайт отличает видео от картинки по расширению (lib/media.ts isVideoUrl)
    import re
    url = "/api/wizard/media/clip/abc.def/clip.mp4"
    assert re.search(r"\.(mp4|webm|mov|m4v)(?:[?#]|$)", url, re.I)


# ── превью каталогов и картинки: стабильный адрес, public immutable ───────────────

def test_catalog_preview_and_poster_are_served_public_immutable(client, monkeypatch) -> None:
    tc, main, calls = client
    frames = []
    monkeypatch.setattr(main.media_proxy, "transcode_frame",
                        lambda src, dst, at, scale=None: (frames.append((src, at, scale)), dst.write_bytes(JPEG)))
    track = main.store.ws().saved_tracks[0]
    url = main.media_proxy.preview_path(main.RUNTIME.session_secret, str(track["localUrl"]), "etag1")
    assert url == main.media_proxy.preview_path(main.RUNTIME.session_secret, str(track["localUrl"]), "etag1")
    clip = tc.get(url)
    assert clip.status_code == 200 and clip.content.startswith(b"FAKEMEDIA")
    assert clip.headers["cache-control"] == "public, max-age=31536000, immutable"
    poster = tc.get(url.replace("/clip.mp4", "/poster.jpg"))
    assert poster.status_code == 200 and poster.content.startswith(JPEG[:2])
    assert poster.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert calls["transcode"] == 1
    # копия уже есть — кадр из неё, в её размере (без повторного масштабирования)
    assert frames[0][1] == main.media_proxy.PREVIEW_POSTER_AT and frames[0][2] is None


def test_catalog_poster_before_copy_reads_the_original(client, monkeypatch) -> None:
    tc, main, calls = client
    frames = []
    monkeypatch.setattr(main.media_proxy, "transcode_frame",
                        lambda src, dst, at, scale=None: (frames.append((src, scale)), dst.write_bytes(JPEG)))
    track = main.store.ws().saved_tracks[0]
    url = main.media_proxy.preview_path(main.RUNTIME.session_secret, str(track["localUrl"]), "etag2")
    assert tc.get(url.replace("/clip.mp4", "/poster.jpg")).status_code == 200
    assert calls["transcode"] == 0 and frames[0][1] == main.media_proxy.SHORT_SIDE_540


def test_catalog_links_are_kind_specific(client) -> None:
    tc, main, _ = client
    clip_token = main.media_proxy.sign(main.RUNTIME.session_secret, {"s": "/static/x.mp4"})
    assert tc.get(f"/api/wizard/media/preview/{clip_token}/clip.mp4").status_code == 404
    assert tc.get(f"/api/wizard/media/image/{clip_token}/image.png").status_code == 404
    assert tc.get("/api/wizard/media/preview/forged.token/poster.jpg").status_code == 404


def test_clip_is_immutable_but_track_stays_short_lived(client) -> None:
    tc, main, _ = client
    track = main.store.ws().saved_tracks[0]
    token = main.media_proxy.sign(main.RUNTIME.session_secret, {"s": str(track["localUrl"])})
    clip = tc.get(f"/api/wizard/media/clip/{token}/clip.mp4")
    assert clip.headers["cache-control"] == "private, max-age=31536000, immutable"
    # трек — по id трека юзера, адрес не адресует содержимое: сутки, как было
    assert tc.get(f"/api/wizard/media/track/{track['id']}").headers["cache-control"] == "private, max-age=86400"


def test_catalog_image_keeps_its_format(client, monkeypatch) -> None:
    tc, main, calls = client
    monkeypatch.setattr(main, "_media_fetch", lambda locator: (lambda path: path.write_bytes(b"PNGSRC")))
    url = main.media_proxy.image_path(main.RUNTIME.session_secret, "s3://fx/fx_assets/frames/exclude.png", "e1")
    assert url.endswith("/image.png")
    r = tc.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert tc.get(url.replace("image.png", "image.jpg")).status_code == 404
    assert calls["transcode"] == 1


def test_proxy_failure_is_an_error_not_the_original(client, monkeypatch) -> None:
    """No Fallback: копию сделать не вышло — 502 с причиной, а не редирект на оригинал."""
    tc, main, _ = client

    def broken(src, dst, args):
        raise main.media_proxy.MediaProxyError("ffmpeg упал")

    monkeypatch.setattr(main.media_proxy, "transcode", broken)
    track = main.store.ws().saved_tracks[0]
    r = tc.get(main.media_proxy.preview_path(main.RUNTIME.session_secret, str(track["localUrl"]), "etag3"))
    assert r.status_code == 502 and "ffmpeg упал" in r.json()["detail"]


def test_object_version_requires_an_etag() -> None:
    assert mp.object_version('"abc-3"') == "abc-3"
    with pytest.raises(mp.MediaProxyError):
        mp.object_version("")


@pytest.mark.skipif(not HAS_FFMPEG, reason="нужен ffmpeg")
def test_catalog_preview_profile_keeps_fps_and_shrinks_landscape(tmp_path: Path) -> None:
    src = tmp_path / "photo.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1440:rate=25:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", "-c:v", "libx264", "-c:a", "aac",
                    str(src)], check=True)
    store = mp.LocalStore(tmp_path / "cache")
    mp.build_preview(store, "preview/aa/p.mp4", lambda dst: dst.write_bytes(src.read_bytes()))
    out = store.path("preview/aa/p.mp4")
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                           "stream=width,height,r_frame_rate", "-of", "csv=p=0", str(out)],
                          capture_output=True, text=True).stdout.strip()
    assert info == "720,540,25/1"  # фото 1920×1440 → 720×540, fps источника
    atoms = _atoms(out)
    assert atoms.index("moov") < atoms.index("mdat")
    audio = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert audio == ""
    mp.build_preview_poster(store, "preview/aa/p.mp4", "preview/aa/p.mp4.poster.jpg", "unused")
    assert store.read("preview/aa/p.mp4.poster.jpg")[:2] == JPEG[:2]


@pytest.mark.skipif(not HAS_FFMPEG, reason="нужен ffmpeg")
def test_catalog_image_is_downscaled_with_alpha(tmp_path: Path) -> None:
    src = tmp_path / "frame.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black@0.0:size=1080x1920,format=rgba",
                    "-frames:v", "1", str(src)], check=True)
    store = mp.LocalStore(tmp_path / "cache")
    mp.build_image(store, "image/aa/f.png", lambda dst: dst.write_bytes(src.read_bytes()))
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,pix_fmt", "-of", "csv=p=0",
                           str(store.path("image/aa/f.png"))], capture_output=True, text=True).stdout.strip()
    width, height, pix_fmt = info.split(",")[:3]
    assert (width, height) == ("720", "1280") and "a" in pix_fmt  # прозрачность рамки сохранена


# ── поллинг примерки и кэш анализа дропа ─────────────────────────────────────────

def test_asr_poll_writes_db_only_when_state_changes(client, monkeypatch) -> None:
    import dataclasses
    _, main, _ = client
    flushes = []
    answers = iter([{"status": "RUNNING"}, {"status": "RUNNING"}, {"status": "COMPLETED", "words": [{"text": "a"}]}])

    class Backend:
        def asr_preview_state(self, job_id):
            return next(answers)

    monkeypatch.setattr(main, "RUNTIME", dataclasses.replace(main.RUNTIME, backend="production"))
    monkeypatch.setattr(main, "_production_backend", lambda: Backend())
    monkeypatch.setattr(main.persistence, "flush_user", lambda uid: flushes.append(uid))
    state = {"key": "k", "status": "RUNNING", "jobId": "j1", "words": []}
    state = main._asr_sync(state)
    state = main._asr_sync(state)
    assert flushes == []  # опрос без изменений — без записи
    state = main._asr_sync(state)
    assert state["status"] == "COMPLETED" and len(flushes) == 1


def test_drop_analysis_is_cached_per_track_and_window(client, monkeypatch) -> None:
    _, main, _ = client
    calls = []

    class Backend:
        def analyze_hook(self, *, audio_s3_url, clip_start_sec, clip_end_sec):
            calls.append((audio_s3_url, clip_start_sec, clip_end_sec))
            return {"bpm": 120.0, "drop_candidates": [{"t": clip_start_sec + 1, "confidence": 0.9}]}

    main._DROPS_CACHE.clear()
    monkeypatch.setattr(main, "_production_backend", lambda: Backend())
    first = main._cached_hook_analysis("s3://raw/t.mp3", 10.0, 22.0)
    assert main._cached_hook_analysis("s3://raw/t.mp3", 10.0, 22.0) == first
    main._cached_hook_analysis("s3://raw/t.mp3", 11.0, 22.0)  # другое окно — свой анализ
    assert len(calls) == 2


def test_drop_candidates_skip_the_window_start(client, monkeypatch) -> None:
    """Дроп впритык к началу окна сборка молча выкидывает вместе с хук-блоком — не предлагаем его."""
    import asyncio
    import dataclasses
    _, main, _ = client
    monkeypatch.setattr(main, "RUNTIME", dataclasses.replace(main.RUNTIME, backend="production"))
    monkeypatch.setattr(main.store, "saved_track", lambda track_id: {"s3Key": "s3://raw/t.mp3"})
    monkeypatch.setattr(main, "_cached_hook_analysis", lambda url, start, end: {"bpm": 120.0, "drop_candidates": [
        {"t": 0.0, "confidence": 0.88}, {"t": 1.0, "confidence": 0.8}, {"t": 4.2, "confidence": 0.74}, {"t": 10.0, "confidence": 0.65}]})
    result = asyncio.run(main.api_drops(trackId="t1", clipFrom="00:00", clipTo="00:12"))
    assert [d["seconds"] for d in result["drops"]] == [4.2, 10.0]
    assert [d["best"] for d in result["drops"]] == [True, False]
