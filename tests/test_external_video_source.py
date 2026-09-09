# -*- coding: utf-8 -*-
"""Ветка «ссылка на YouTube» для F6-прогрева: разбор ссылок, команда, ошибки.

Сеть здесь не трогается: yt-dlp подменяется фейковым раннером. Проверяем ровно
то, что можно проверить без прокси и без токен-провайдера, — всё остальное
всплывёт только на живом прогоне и честно вынесено в «осталось».
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mlcore.media.external_video import (
    MAX_SECTION_SEC,
    ExternalClipRequest,
    ExternalFetchBlocked,
    ExternalFetchError,
    build_command,
    external_source_enabled,
    fetch_section,
    is_supported_url,
    validate_request,
    video_id,
)


# ---------- ссылки ----------

@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=42", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/abc123XYZ_-", "abc123XYZ_-"),
    ("https://m.youtube.com/watch?v=abc123XYZ_-&list=PL1", "abc123XYZ_-"),
    ("https://www.youtube.com/embed/abc123XYZ_-", "abc123XYZ_-"),
])
def test_video_id_is_extracted_from_every_link_shape(url, expected):
    assert is_supported_url(url)
    assert video_id(url) == expected


@pytest.mark.parametrize("url", [
    "https://vimeo.com/12345",
    "https://example.com/watch?v=abc",
    "ftp://youtube.com/watch?v=abc",
    "не ссылка",
    "",
])
def test_foreign_and_broken_links_are_refused(url):
    assert not is_supported_url(url)


def test_youtube_link_without_an_id_is_refused():
    req = ExternalClipRequest(url="https://www.youtube.com/feed/trending", start_sec=0.0, end_sec=5.0)
    with pytest.raises(ExternalFetchError, match="не нашёлся id"):
        validate_request(req)


# ---------- окно ----------

def test_window_must_be_long_enough():
    with pytest.raises(ExternalFetchError, match="слишком коротк"):
        validate_request(ExternalClipRequest(
            url="https://youtu.be/abc123XYZ_-", start_sec=10.0, end_sec=10.4))


def test_window_must_not_be_longer_than_the_warm_up_cap():
    with pytest.raises(ExternalFetchError, match="слишком длинн"):
        validate_request(ExternalClipRequest(
            url="https://youtu.be/abc123XYZ_-", start_sec=0.0, end_sec=MAX_SECTION_SEC + 1))


def test_a_sane_window_passes():
    validate_request(ExternalClipRequest(
        url="https://youtu.be/abc123XYZ_-", start_sec=12.0, end_sec=19.0))


# ---------- командная строка ----------

def _req():
    return ExternalClipRequest(url="https://youtu.be/abc123XYZ_-", start_sec=12.0, end_sec=19.0)


def test_command_downloads_only_the_requested_section(monkeypatch, tmp_path):
    monkeypatch.delenv("YTDLP_PROXY", raising=False)
    cmd = build_command(_req(), out_path=tmp_path / "out.mp4")
    assert "--download-sections" in cmd
    assert cmd[cmd.index("--download-sections") + 1] == "*12.000-19.000"
    # Без этого начало вырезки уезжает к предыдущему ключевому кадру, и прогрев
    # перестаёт попадать встык к дропу.
    assert "--force-keyframes-at-cuts" in cmd
    assert "--no-playlist" in cmd


def test_command_carries_proxy_pot_and_cookies_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("YTDLP_PROXY", "http://user:pass@proxy.example:8000")
    monkeypatch.setenv("YTDLP_POT_BASE_URL", "http://bgutil:4416")
    monkeypatch.setenv("YTDLP_COOKIES_FILE", "/run/secrets/yt_cookies.txt")
    cmd = build_command(_req(), out_path=tmp_path / "out.mp4")
    assert cmd[cmd.index("--proxy") + 1] == "http://user:pass@proxy.example:8000"
    assert cmd[cmd.index("--cookies") + 1] == "/run/secrets/yt_cookies.txt"
    assert "youtubepot-bgutilhttp:base_url=http://bgutil:4416" in cmd


def test_command_omits_optional_flags_when_unset(monkeypatch, tmp_path):
    for key in ("YTDLP_PROXY", "YTDLP_POT_BASE_URL", "YTDLP_COOKIES_FILE", "YTDLP_EXTRA_ARGS"):
        monkeypatch.delenv(key, raising=False)
    cmd = build_command(_req(), out_path=tmp_path / "out.mp4")
    assert "--proxy" not in cmd
    assert "--cookies" not in cmd
    assert "--extractor-args" not in cmd


def test_url_is_passed_after_a_separator(monkeypatch, tmp_path):
    """URL идёт после `--`, иначе ссылка, начинающаяся с дефиса, будет разобрана
    как флаг."""
    cmd = build_command(_req(), out_path=tmp_path / "out.mp4")
    assert cmd[-2] == "--"
    assert cmd[-1] == "https://youtu.be/abc123XYZ_-"


# ---------- запуск ----------

class _Proc:
    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = b""


def test_fetch_returns_the_downloaded_file(tmp_path):
    def runner(cmd, **kwargs):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"fake-mp4-bytes")
        return _Proc()

    out = fetch_section(_req(), work_dir=tmp_path, runner=runner)
    assert out.exists() and out.stat().st_size > 0
    assert out.name == "yt_abc123XYZ_-.mp4"


def test_bot_detection_is_reported_as_a_separate_failure(tmp_path):
    """Отличать «нас забанили» от «ссылка кривая» важно: реакция разная —
    во втором случае юзеру есть что исправить, в первом нет."""
    def runner(cmd, **kwargs):
        return _Proc(1, b"ERROR: Sign in to confirm you're not a bot")

    with pytest.raises(ExternalFetchBlocked):
        fetch_section(_req(), work_dir=tmp_path, runner=runner)


@pytest.mark.parametrize("stderr", [
    b"ERROR: ... PO Token is required ...",
    b"ERROR: unable to download API page",
    b"ERROR: HTTP Error 403: Forbidden",
])
def test_po_token_and_403_also_count_as_blocked(tmp_path, stderr):
    with pytest.raises(ExternalFetchBlocked):
        fetch_section(_req(), work_dir=tmp_path, runner=lambda cmd, **kw: _Proc(1, stderr))


def test_ordinary_errors_stay_ordinary(tmp_path):
    def runner(cmd, **kwargs):
        return _Proc(1, b"ERROR: Video unavailable. This video is private")

    with pytest.raises(ExternalFetchError) as e:
        fetch_section(_req(), work_dir=tmp_path, runner=runner)
    assert not isinstance(e.value, ExternalFetchBlocked)
    assert "private" in str(e.value)


def test_missing_ytdlp_binary_says_what_to_install(tmp_path):
    def runner(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    with pytest.raises(ExternalFetchError, match="yt-dlp не найден"):
        fetch_section(_req(), work_dir=tmp_path, runner=runner)


def test_timeout_is_reported_not_swallowed(tmp_path):
    def runner(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    with pytest.raises(ExternalFetchError, match="не уложился"):
        fetch_section(_req(), work_dir=tmp_path, runner=runner, timeout_s=1)


def test_empty_output_is_an_error(tmp_path):
    def runner(cmd, **kwargs):
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"")
        return _Proc()

    with pytest.raises(ExternalFetchError, match="пустой файл"):
        fetch_section(_req(), work_dir=tmp_path, runner=runner)


def test_a_differently_named_container_is_still_found(tmp_path):
    """yt-dlp иногда игнорирует merge-output-format и кладёт .webm/.mkv."""
    def runner(cmd, **kwargs):
        out = Path(cmd[cmd.index("-o") + 1])
        out.with_suffix(".webm").write_bytes(b"fake")
        return _Proc()

    got = fetch_section(_req(), work_dir=tmp_path, runner=runner)
    assert got.suffix == ".webm"


def test_network_is_never_touched_before_the_window_is_validated(tmp_path):
    """Отказ по окну должен случиться ДО запуска yt-dlp — иначе мы платим
    трафиком и временем за заведомо негодный запрос."""
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return _Proc()

    bad = ExternalClipRequest(url="https://youtu.be/abc123XYZ_-", start_sec=0.0, end_sec=999.0)
    with pytest.raises(ExternalFetchError):
        fetch_section(bad, work_dir=tmp_path, runner=runner)
    assert calls == []


# ---------- флаг ----------

def test_source_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("EXTERNAL_VIDEO_SOURCE_ENABLED", raising=False)
    assert external_source_enabled() is False
    monkeypatch.setenv("EXTERNAL_VIDEO_SOURCE_ENABLED", "0")
    assert external_source_enabled() is False
    monkeypatch.setenv("EXTERNAL_VIDEO_SOURCE_ENABLED", "1")
    assert external_source_enabled() is True


# ---------- контракт эндпоинта ----------

def test_request_schema_rejects_a_backwards_window():
    from services.orchestrator.schemas import FetchExternalVideoRequest

    with pytest.raises(ValueError, match="end_sec must be > start_sec"):
        FetchExternalVideoRequest(url="https://youtu.be/x", start_sec=10.0, end_sec=5.0)


def test_response_schema_carries_what_the_bot_needs_for_f6():
    from services.orchestrator.schemas import FetchExternalVideoResponse

    resp = FetchExternalVideoResponse(
        video_url="s3://raw/external_video/x.mp4",
        width=1920, height=1080, duration_sec=7.0, has_audio=True,
    )
    # ровно те поля, которые едут в f6_video_* при enqueue
    assert resp.video_url and resp.width and resp.height
    assert resp.duration_sec == 7.0 and resp.has_audio is True
