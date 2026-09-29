from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_BACKEND = REPO_ROOT / "web_app" / "backend"


def _module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(WEB_BACKEND))
    sys.modules.pop("app.tiktok_api", None)
    return importlib.import_module("app.tiktok_api")


def test_direct_post_info_contains_review_fields_and_omits_aigc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module(monkeypatch)

    post_info = module.build_video_post_info(
        title="new track",
        privacy_level="PUBLIC_TO_EVERYONE",
        comments=True,
        duet=False,
        stitch=True,
        cover_timestamp_ms=1250,
        brand_content=True,
        brand_organic=False,
    )

    assert post_info == {
        "title": "new track",
        "privacy_level": "PUBLIC_TO_EVERYONE",
        "disable_duet": True,
        "disable_stitch": False,
        "disable_comment": False,
        "video_cover_timestamp_ms": 1250,
        "brand_content_toggle": True,
        "brand_organic_toggle": False,
    }
    assert "is_aigc" not in post_info


@pytest.mark.parametrize(
    ("setting", "creator_flag", "error_code"),
    (
        ("comments", "comment_disabled", "comment_unavailable"),
        ("duet", "duet_disabled", "duet_unavailable"),
        ("stitch", "stitch_disabled", "stitch_unavailable"),
    ),
)
def test_creator_disabled_interaction_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    creator_flag: str,
    error_code: str,
) -> None:
    module = _module(monkeypatch)
    requested = {"comments": False, "duet": False, "stitch": False}
    requested[setting] = True

    with pytest.raises(module.TikTokPostValidationError) as caught:
        module.validate_video_post_settings(
            {
                "privacy_level_options": ["PUBLIC_TO_EVERYONE"],
                creator_flag: True,
            },
            privacy_level="PUBLIC_TO_EVERYONE",
            brand_content=False,
            **requested,
        )

    assert caught.value.code == error_code


def test_branded_content_cannot_be_private(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)

    with pytest.raises(module.TikTokPostValidationError) as caught:
        module.validate_video_post_settings(
            {"privacy_level_options": ["SELF_ONLY"]},
            privacy_level="SELF_ONLY",
            comments=False,
            duet=False,
            stitch=False,
            brand_content=True,
        )

    assert caught.value.code == "branded_content_private"


def test_unavailable_creator_privacy_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)

    with pytest.raises(module.TikTokPostValidationError) as caught:
        module.validate_video_post_settings(
            {"privacy_level_options": ["SELF_ONLY"]},
            privacy_level="PUBLIC_TO_EVERYONE",
            comments=False,
            duet=False,
            stitch=False,
            brand_content=False,
        )

    assert caught.value.code == "privacy_unavailable"


def _config_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(WEB_BACKEND))
    sys.modules.pop("app.tiktok_config", None)
    return importlib.import_module("app.tiktok_config")


def _load_config(monkeypatch: pytest.MonkeyPatch, allowed: str) -> Any:
    module = _config_module(monkeypatch)
    monkeypatch.setattr(module, "_env_loaded", True, raising=False)
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "sandbox-key")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "sandbox-secret")
    monkeypatch.setenv("TIKTOK_UPLOAD_SOURCE", "FILE_UPLOAD")
    monkeypatch.setenv("TIKTOK_ALLOWED_USER_IDS", allowed)
    return module.load()


def test_sandbox_access_is_limited_to_listed_people(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _load_config(monkeypatch, "user_1, Owner@Blast808.com ,7654321")

    assert cfg.configured
    # список принимает id, почту и telegram chat id, регистр не важен
    assert cfg.allows("user_1", None, None)
    assert cfg.allows("user_9", "owner@blast808.com", None)
    assert cfg.allows("user_9", None, 7654321)
    # чужой человек в песочницу не попадает: для него интеграция «не настроена»
    assert not cfg.allows("user_9", "someone@example.com", 111)
    assert not cfg.allows(None, None, None)


def test_empty_allowlist_closes_the_integration_and_star_opens_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # пусто = никому: «случайно всем» в песочнице хуже, чем «никому»
    assert not _load_config(monkeypatch, "").allows("user_1", "owner@blast808.com", 1)
    # состояние после одобрения заявки — открыть всем можно только явной звёздочкой
    assert _load_config(monkeypatch, "*").allows("whoever", None, None)


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (3_000_000, (3_000_000, 1)),          # меньше 5 МБ — одним чанком целиком
        (7_000_000, (7_000_000, 1)),          # от 5 до 10 МБ — тоже один чанк
        (10_000_000, (10_000_000, 1)),
        (23_456_789, (10_000_000, 2)),        # остаток уходит в последний чанк, а не в третий
        (30_000_000, (10_000_000, 3)),
    ],
)
def test_file_upload_chunk_count_rounds_down(monkeypatch: pytest.MonkeyPatch, size: int, expected: tuple[int, int]) -> None:
    module = _module(monkeypatch)
    chunk_size, total = module.chunk_plan(size)
    assert (chunk_size, total) == expected
    # последний чанк забирает остаток и не превышает лимит TikTok в 128 МБ
    last = size - chunk_size * (total - 1)
    assert chunk_size <= last <= 128_000_000


def test_publish_goes_through_its_own_foreign_proxy_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # TikTok не принимает загрузку с российских IP: без прокси публикация с сервера в Москве
    # падает у TikTok с FAILED / internal. Прокси LLM пускает только Google — у TikTok свой.
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    from src.outbound_proxy import OUTBOUND_PROXY_URL, TIKTOK_PUBLISH_PROXY_URL

    monkeypatch.delenv("TIKTOK_PUBLISH_PROXY", raising=False)
    assert _load_config(monkeypatch, "*").publish_proxy == TIKTOK_PUBLISH_PROXY_URL
    assert TIKTOK_PUBLISH_PROXY_URL != OUTBOUND_PROXY_URL
    monkeypatch.setenv("TIKTOK_PUBLISH_PROXY", "direct")
    assert _load_config(monkeypatch, "*").publish_proxy == ""
    monkeypatch.setenv("TIKTOK_PUBLISH_PROXY", "http://proxy.example:8080")
    assert _load_config(monkeypatch, "*").publish_proxy == "http://proxy.example:8080"


def test_proxy_opener_is_scoped_to_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    with_proxy = module._opener("http://proxy.example:8080")
    handlers = [h for h in with_proxy.handlers if h.__class__.__name__ == "ProxyHandler"]
    assert handlers and handlers[0].proxies == {"http": "http://proxy.example:8080", "https": "http://proxy.example:8080"}


def test_socks_proxy_tunnels_https_and_keeps_env_proxies_out(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy.example:3128")
    opener = module._opener("socks5h://user:pa%40ss@socks.example:1080")
    names = [h.__class__.__name__ for h in opener.handlers]
    assert "_SocksHTTPSHandler" in names
    # HTTPS_PROXY из окружения не должен наложиться поверх SOCKS
    assert not [h for h in opener.handlers if getattr(h, "proxies", None)]

    seen = {}

    def fake_create_connection(address, **kwargs):
        seen.update(address=address, **kwargs)
        raise OSError("stop before the network")

    import socks
    monkeypatch.setattr(socks, "create_connection", fake_create_connection)
    with pytest.raises(OSError):
        opener.open("https://open.tiktokapis.com/v2/post/publish/status/fetch/", timeout=5)
    assert seen["address"] == ("open.tiktokapis.com", 443)
    assert (seen["proxy_addr"], seen["proxy_port"], seen["proxy_rdns"]) == ("socks.example", 1080, True)
    assert (seen["proxy_username"], seen["proxy_password"]) == ("user", "pa@ss")


def test_dead_publish_route_is_reported_as_ours_not_tiktoks(monkeypatch: pytest.MonkeyPatch) -> None:
    # Прокси, который не туннелирует до TikTok, раньше ронял /api/tiktok/post в 500,
    # а UI писал «TikTok rejected the video» — хотя TikTok запроса даже не видел.
    module = _module(monkeypatch)

    class DeadOpener:
        def open(self, req, timeout=None):
            raise module.urllib.error.URLError(OSError("Tunnel connection failed: 502 Bad Gateway"))

    monkeypatch.setattr(module, "_opener", lambda proxy="": DeadOpener())
    with pytest.raises(module.TikTokApiError) as caught:
        module.fetch_publish_status("token", "pub_1", "http://proxy.example:8080")
    assert caught.value.code == "route_unavailable"
    assert caught.value.status == 502
    assert "proxy" in str(caught.value)


def test_chunk_upload_streams_the_body_so_a_slow_link_does_not_time_out(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Через прокси публикации ~100 КБ/с: весь чанк одним sendall упирался в таймаут 120 с
    # («The write operation timed out»). Тело должно уходить потоком, с известной длиной.
    module = _module(monkeypatch)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 6_000_000)
    monkeypatch.setattr(module, "_json_request", lambda *a, **k: {"publish_id": "p1", "upload_url": "https://upload.example/u"})
    sent = []

    class Resp:
        status = 201

        def read(self, n=-1):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Opener:
        def open(self, req, timeout=None):
            sent.append((hasattr(req.data, "read"), req.get_header("Content-length"), len(req.data.read()), timeout))
            return Resp()

    monkeypatch.setattr(module, "_opener", lambda proxy="": Opener())
    module.init_direct_post_file("token", {"privacy_level": "SELF_ONLY"}, video, "socks5h://p:1")
    assert sent == [(True, "6000000", 6_000_000, 120)]


def test_file_upload_runs_in_background_and_status_says_sending(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Заливка через прокси идёт минуты: POST отвечает сразу после init, файл льётся в фоне,
    # а статус до конца заливки — наш SENDING (шаг 1 на странице), потом — ответ TikTok.
    import importlib
    import sys
    import threading

    from tests.test_web_asr_preview import _env

    _env(monkeypatch)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    api = main.tiktok_api

    video_file = tmp_path / "v.mp4"
    video_file.write_bytes(b"x" * 1000)
    cleanup = tmp_path / "tmpdir"
    cleanup.mkdir()
    release = threading.Event()
    uploaded = threading.Event()

    monkeypatch.setattr(api, "init_file_upload", lambda token, info, path, proxy: {"publish_id": "pub_ok", "upload_url": "https://up/u"})

    def slow_upload(url, path, proxy):
        release.wait(5)
        uploaded.set()
        return {"transfer": [{"chunk": 1, "status": 201}], "upload_host": "up"}

    monkeypatch.setattr(api, "upload_file_chunks", slow_upload)
    monkeypatch.setattr(main, "_tiktok_ready", lambda cfg=None: True)
    monkeypatch.setattr(main, "_access_token", lambda: "token")
    monkeypatch.setattr(api, "fetch_publish_status", lambda token, pid, proxy="": {"status": "PROCESSING_DOWNLOAD"})

    video: dict = {}
    started = main._start_file_upload("token", {}, video_file, "", video, cleanup=cleanup)
    assert started == {"publish_id": "pub_ok", "status": "SENDING"}
    assert main.api_tiktok_post_status("pub_ok")["status"] == "SENDING"
    release.set()
    assert uploaded.wait(5)
    for _ in range(250):  # поток сначала пишет состояние, потом удаляет временную копию
        if main._TIKTOK_UPLOADS["pub_ok"]["state"] == "uploaded" and not cleanup.exists():
            break
        threading.Event().wait(0.02)
    assert main.api_tiktok_post_status("pub_ok")["status"] == "PROCESSING_DOWNLOAD"
    assert video["tiktokTransfer"]["chunks"][0]["status"] == 201
    assert not cleanup.exists()  # временную копию ролика убрали

    def broken_upload(url, path, proxy):
        raise api.TikTokApiError("route_unavailable", "TikTok unreachable (proxy): timed out", 502)

    monkeypatch.setattr(api, "init_file_upload", lambda token, info, path, proxy: {"publish_id": "pub_bad", "upload_url": "https://up/u"})
    monkeypatch.setattr(api, "upload_file_chunks", broken_upload)
    main._start_file_upload("token", {}, video_file, "", {})
    for _ in range(50):
        if main._TIKTOK_UPLOADS["pub_bad"]["state"] == "failed":
            break
        threading.Event().wait(0.02)
    failed = main.api_tiktok_post_status("pub_bad")
    assert (failed["status"], failed["fail_reason"]) == ("FAILED", "route_unavailable")
