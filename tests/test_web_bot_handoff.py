"""Ссылка «на сайт» из публичного бота: вход по токену + проект с треком из бота."""
from __future__ import annotations

import dataclasses
import importlib
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_web_asr_preview import _env

TOKEN = "t" * 32
CHAT = 777000111


class _Billing:
    def __init__(self, record: dict[str, Any] | None, *, allowed: bool = True) -> None:
        self.record = record
        self.allowed = allowed
        self.consumed: list[tuple[int, str]] = []
        self.results: list[dict[str, Any]] = []
        self.released = 0
        self.max_redeems: int | None = None  # 1 — одноразовая ссылка (/site, напоминания)

    async def redeem_handoff(self, token: str) -> dict[str, Any] | None:
        assert token == TOKEN
        if self.record is None:
            return None
        if self.max_redeems is not None and self.record["redeem_count"] >= self.max_redeems:
            return None
        self.record["redeem_count"] += 1
        return {**self.record, "result": dict(self.record["result"])}

    async def peek_handoff_owner(self, token: str) -> int | None:
        assert token == TOKEN
        if self.record is None:
            return None
        if self.max_redeems is not None and self.record["redeem_count"] >= self.max_redeems:
            return None
        return int(self.record["tg_id"])

    async def release_handoff(self, token: str) -> None:
        assert token == TOKEN
        self.released += 1
        self.record["redeem_count"] = max(0, self.record["redeem_count"] - 1)

    async def set_handoff_result(self, token: str, result: dict[str, Any]) -> None:
        self.results.append(result)
        self.record["result"] = result

    async def can_upload_track(self, tg_id: int, audio_hash: str) -> bool:
        return self.allowed

    async def consume_track(self, tg_id: int, audio_hash: str) -> str:
        self.consumed.append((tg_id, audio_hash))
        return "consumed"


class _Backend:
    def __init__(self) -> None:
        self.registered: list[str] = []

    def register_bot_track(self, s3_url: str, *, filename: str) -> dict[str, str]:
        self.registered.append(s3_url)
        return {"s3_url": s3_url, "playback_url": "https://s3.example/presigned.mp3"}


def _track_record() -> dict[str, Any]:
    return {
        "tg_id": CHAT,
        "kind": "track",
        "payload": {
            "audioS3Url": "s3://raw-audio/raw_audio/777000111/20261002_x_song.mp3",
            "audioHash": "a" * 64,
            "filename": "My Song.mp3",
            "profile": {"name": "Лена", "surname": "", "username": "lena_beats"},
        },
        "result": {},
        "redeem_count": 0,
    }


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


def _as_user(main) -> None:
    """Проверки — в воркспейсе того, кого залогинила ссылка (вне запроса контекст демо)."""
    main.store.use_user(main.auth_store.get_user_by_chat(CHAT)["id"])


def _production(monkeypatch: pytest.MonkeyPatch, main, billing: _Billing, backend: _Backend | None = None) -> None:
    monkeypatch.setattr(main, "RUNTIME", dataclasses.replace(main.RUNTIME, backend="production"))
    monkeypatch.setattr(main, "_billing_backend", lambda: billing)
    monkeypatch.setattr(main, "_production_backend", lambda: backend or _Backend())


def test_track_link_creates_account_project_and_track(client, monkeypatch) -> None:
    tc, main = client
    billing, backend = _Billing(_track_record()), _Backend()
    _production(monkeypatch, main, billing, backend)

    r = tc.post("/api/auth/handoff", json={"token": TOKEN})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["redirectTo"] == f"/app/generate?project={body['projectId']}"
    assert body["track"]["s3Key"] == "s3://raw-audio/raw_audio/777000111/20261002_x_song.mp3"
    assert body["track"]["audioHash"] == "a" * 64
    user = main.auth_store.get_user_by_chat(CHAT)
    assert user is not None and user["name"] == "Лена" and user["tgUsername"] == "lena_beats"
    # бот входа этот человек не запускал — уведомления сайта пойдут от публичного бота
    assert main.auth_store.notify_bot_for_chat(CHAT) == "public"
    _as_user(main)
    assert main.store.get_project(body["projectId"]) is not None
    assert backend.registered == [body["track"]["s3Key"]]
    # слот трека тратится при первой генерации на сайте, а не при открытии ссылки
    assert billing.consumed == []
    assert billing.results[-1] == {"projectId": body["projectId"], "trackId": body["track"]["id"]}


def test_reopening_the_link_lands_on_the_same_project(client, monkeypatch) -> None:
    tc, main = client
    billing, backend = _Billing(_track_record()), _Backend()
    _production(monkeypatch, main, billing, backend)
    first = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()

    second = tc.post("/api/auth/handoff", json={"token": TOKEN})

    assert second.status_code == 200
    body = second.json()
    assert body["projectId"] == first["projectId"] and body["repeat"] is True
    assert body["track"]["id"] == first["track"]["id"]
    assert body["created"] is False
    _as_user(main)
    assert [p["id"] for p in main.store.ws().projects] == [first["projectId"]]
    assert len(backend.registered) == 1 and billing.consumed == []


def test_expired_link_is_410_with_code(client, monkeypatch) -> None:
    tc, main = client
    _production(monkeypatch, main, _Billing(None))
    r = tc.post("/api/auth/handoff", json={"token": TOKEN})
    assert r.status_code == 410
    assert r.json()["detail"]["code"] == "handoff_expired"


def test_no_track_slot_still_brings_the_track_with_a_warning(client, monkeypatch) -> None:
    """Слот не тратим при открытии: трек кладём в проект, генерация упрётся в лимит треков."""
    tc, main = client
    billing, backend = _Billing(_track_record(), allowed=False), _Backend()
    _production(monkeypatch, main, billing, backend)
    body = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    assert body["trackError"] == "tracks_limit" and body["track"] is not None
    assert backend.registered and billing.consumed == []


def test_site_link_only_logs_in(client, monkeypatch) -> None:
    tc, main = client
    record = {"tg_id": CHAT, "kind": "site", "payload": {"profile": {"name": "Лена"}}, "result": {}, "redeem_count": 0}
    billing = _Billing(record)
    _production(monkeypatch, main, billing)
    body = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    assert body["redirectTo"] == "/app" and "projectId" not in body
    assert main.auth_store.get_user_by_chat(CHAT) is not None
    assert billing.consumed == []
    _as_user(main)
    assert main.store.ws().projects == []


def test_existing_account_is_reused(client, monkeypatch) -> None:
    tc, main = client
    existing = main.auth_store.create_user_from_telegram(CHAT, {"name": "Старое имя"})
    _production(monkeypatch, main, _Billing(_track_record()))
    body = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    assert body["created"] is False
    assert main.auth_store.get_user_by_chat(CHAT)["id"] == existing["id"]
    assert main.auth_store.get_user_by_chat(CHAT)["name"] == "Старое имя"


def test_mock_backend_refuses_explicitly(client) -> None:
    tc, _ = client
    r = tc.post("/api/auth/handoff", json={"token": TOKEN})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "handoff_unavailable"


def test_bot_track_must_live_in_the_raw_audio_bucket(monkeypatch) -> None:
    _env(monkeypatch)
    sys.modules.pop("app.production_backend", None)
    pb = importlib.import_module("app.production_backend")
    heads: list[tuple[str, str]] = []
    backend = pb.ProductionBackend.__new__(pb.ProductionBackend)
    backend.config = SimpleNamespace(raw_audio_bucket="raw-audio")
    backend._s3 = SimpleNamespace(head_object=lambda Bucket, Key: heads.append((Bucket, Key)))
    monkeypatch.setattr(backend, "_presign", lambda bucket, key, **kw: f"https://s3/{bucket}/{key}")

    with pytest.raises(pb.ProductionBackendError):
        backend.register_bot_track("s3://other-bucket/x.mp3", filename="x.mp3")
    assert heads == []
    out = backend.register_bot_track("s3://raw-audio/raw_audio/1/x.mp3", filename="x.mp3")
    assert out == {"s3_url": "s3://raw-audio/raw_audio/1/x.mp3", "playback_url": "https://s3/raw-audio/raw_audio/1/x.mp3"}
    assert heads == [("raw-audio", "raw_audio/1/x.mp3")]


def test_s3_failure_leaves_no_empty_project(client, monkeypatch) -> None:
    """Сбой на проверке трека — 503 без пустого проекта; повтор заводит ровно один."""
    tc, main = client
    billing = _Billing(_track_record())

    class _Flaky(_Backend):
        def __init__(self) -> None:
            super().__init__()
            self.fail = True

        def register_bot_track(self, s3_url: str, *, filename: str) -> dict[str, str]:
            if self.fail:
                raise TimeoutError("s3 head timeout")
            return super().register_bot_track(s3_url, filename=filename)

    backend = _Flaky()
    _production(monkeypatch, main, billing, backend)
    assert tc.post("/api/auth/handoff", json={"token": TOKEN}).status_code == 503
    _as_user(main)
    assert main.store.ws().projects == []
    backend.fail = False
    body = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    _as_user(main)
    assert [p["id"] for p in main.store.ws().projects] == [body["projectId"]]
    assert billing.consumed == []


CHAT2 = 777000222


def test_new_link_for_the_same_track_reuses_the_project(client, monkeypatch) -> None:
    """Напоминание, перевыпуск, /site — новый токен на тот же трек. Второй проект не
    заводим и второй слот не тратим: ведём в проект первой ссылки."""
    tc, main = client
    billing, backend = _Billing(_track_record()), _Backend()
    _production(monkeypatch, main, billing, backend)
    first = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    billing.record["result"] = {}  # как будто это другой, свежий токен

    second = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()

    assert second["projectId"] == first["projectId"] and second["repeat"] is True
    assert second["track"]["id"] == first["track"]["id"]
    _as_user(main)
    assert [p["id"] for p in main.store.ws().projects] == [first["projectId"]]
    assert len(backend.registered) == 1 and billing.consumed == []
    assert billing.results[-1] == {"projectId": first["projectId"], "trackId": first["track"]["id"]}
    assert main._HANDOFF_LOCKS == {}  # замок не копится после запроса


def test_malformed_token_is_the_expired_screen(client, monkeypatch) -> None:
    tc, main = client
    _production(monkeypatch, main, _Billing(_track_record()))
    for token in ("short", ""):
        r = tc.post("/api/auth/handoff", json={"token": token})
        assert r.status_code == 410 and r.json()["detail"]["code"] == "handoff_expired"


def test_other_account_in_the_browser_needs_confirmation(client, monkeypatch) -> None:
    tc, main = client
    billing = _Billing(_track_record())
    _production(monkeypatch, main, billing)
    assert tc.post("/api/auth/handoff", json={"token": TOKEN}).status_code == 200
    first_user = main.auth_store.get_user_by_chat(CHAT)
    billing.record = {**_track_record(), "tg_id": CHAT2}

    r = tc.post("/api/auth/handoff", json={"token": TOKEN})

    redeemed = billing.record["redeem_count"]
    assert r.status_code == 409 and r.json()["detail"]["code"] == "handoff_other_account"
    assert main.auth_store.get_user_by_chat(CHAT2) is None  # молча ничего не заводим
    assert billing.record["redeem_count"] == redeemed  # вопрос не тратит одноразовую ссылку
    forced = tc.post("/api/auth/handoff", json={"token": TOKEN, "force": True})
    assert forced.status_code == 200 and forced.json()["created"] is True
    second_user = main.auth_store.get_user_by_chat(CHAT2)
    assert second_user["id"] != first_user["id"]
    # сессия теперь второго аккаунта: та же ссылка без force больше не спрашивает
    assert tc.post("/api/auth/handoff", json={"token": TOKEN}).status_code == 200
    billing.record = {**_track_record(), "tg_id": CHAT}
    assert tc.post("/api/auth/handoff", json={"token": TOKEN}).status_code == 409


def _google_session(tc, main, billing) -> dict:
    """Сессия аккаунта без Telegram (как заведённый через Google)."""
    tc.post("/api/auth/handoff", json={"token": TOKEN})
    google_user = main.auth_store.get_user_by_chat(CHAT)
    google_user["tgChatId"] = None
    google_user["email"] = "lena@example.com"
    billing.record = {**_track_record(), "tg_id": CHAT2}
    return google_user


def test_account_without_telegram_is_asked_before_linking(client, monkeypatch) -> None:
    """В браузере аккаунт без Telegram (Google): молча chat_id не привязываем — сперва
    вопрос «Привязать Telegram к аккаунту …?», и одноразовая ссылка на нём не тратится."""
    tc, main = client
    billing = _Billing(_track_record())
    _production(monkeypatch, main, billing)
    google_user = _google_session(tc, main, billing)
    redeemed = billing.record["redeem_count"]

    r = tc.post("/api/auth/handoff", json={"token": TOKEN})

    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "handoff_link_account" and detail["email"] == "lena@example.com"
    assert billing.record["redeem_count"] == redeemed
    assert main.auth_store.get_user_by_chat(CHAT2) is None

    body = tc.post("/api/auth/handoff", json={"token": TOKEN, "link": True}).json()

    assert body["created"] is False
    linked = main.auth_store.get_user_by_chat(CHAT2)
    assert linked["id"] == google_user["id"] and linked["tgVerified"] is True


def test_account_without_telegram_can_log_in_separately(client, monkeypatch) -> None:
    """«Войти отдельно»: из аккаунта без Telegram выходим, по Telegram заводится свой."""
    tc, main = client
    billing = _Billing(_track_record())
    _production(monkeypatch, main, billing)
    google_user = _google_session(tc, main, billing)

    body = tc.post("/api/auth/handoff", json={"token": TOKEN, "force": True}).json()

    assert body["created"] is True
    separate = main.auth_store.get_user_by_chat(CHAT2)
    assert separate["id"] != google_user["id"]
    assert google_user.get("tgChatId") is None  # Google-аккаунт не тронут


# ── «Докрутить на сайте»: монтаж ролика сразу на столе ─────────────────────────

class _RemixBackend(_Backend):
    """Оркестратор с двумя готовыми роликами бота (edit_state) и клоном примерки."""

    def __init__(self, *, clips_gone: bool = False) -> None:
        super().__init__()
        self.cloned: list[str] = []
        self.clone_keys: list[str] = []
        self.picks: list[dict[str, Any]] = []
        self.clips_gone = clips_gone

    def remix_catalog(self):
        from app import bot_import

        return bot_import.ImportCatalog(
            subtitle_modes={"Brat": "brat_5th"},
            footage={"Неон": {"rotationTheme": "visual", "rotationTagsGroup": "neon", "renderPreset": "vertical", "plane": "vibes"}},
        )

    def job_edit_state(self, job_id: str) -> dict[str, Any]:
        cuts = [12.0, 15.0]
        bounds = [10.5, *cuts, 19.5]
        return {
            "job_id": job_id, "status": "SUCCEEDED",
            "request": {"subtitles_mode": "brat_5th", "user_drop_t": 15.0, "hook_enabled": True, "effect_hook": "hook_light",
                        "effect_transition": "minimax", "rotation_theme": "visual", "rotation_tags_group": "neon", "bg_mode": "footage"},
            "window": {"clip_start_abs": 10.0, "clip_end_abs": 20.0},
            "footage_plan": {"version": 1, "clip_start_abs": 10.5, "clip_end_abs": 19.5, "switch_points_abs": cuts, "clips": [
                {"file_name": f"{job_id}-{i}.mp4", "fit_mode": "cover", "in_point": bounds[i], "out_point": bounds[i + 1],
                 "start_time": bounds[i], "source_offset_sec": 0.0} for i in range(3)]},
            "footage_plan_meta": {"bg_mode": "footage", "exact_slot": True},
            "switch_points_abs": cuts,
            "words": [{"text": "раз", "t_start": 10.2, "t_end": 10.6}],
            "asr": {"available": True, "mode": "local_ctc", "alignment_backend": "local_ctc", "reference_text": "раз два"},
        }

    def storyboard_pick(self, *, group_name, clip_start_abs, clip_end_abs, switch_points_abs, videos):
        self.picks.append({"group": group_name, "start": clip_start_abs, "end": clip_end_abs, "videos": videos})
        if self.clips_gone:
            from app.production_backend import ProductionBackendError

            raise ProductionBackendError("clip is not in the inventory of this slot")
        return {"videos": [{"clips": [{"file_name": name, "in_point": 0, "out_point": 1, "preview_url": "https://p/x.mp4"}
                                      for _, name in sorted(v["pins"].items(), key=lambda kv: int(kv[0]))], "plan": {}}
                           for v in videos]}

    def asr_preview_from_job(self, source_job_id: str, *, clone_key: str) -> str:
        self.cloned.append(source_job_id)
        self.clone_keys.append(clone_key)
        return "asr-clone-1"

    def asr_preview_state(self, job_id: str) -> dict[str, Any]:
        return {"status": "COMPLETED", "words": [{"text": "раз", "tStart": 10.2, "tEnd": 10.6, "weak": False}],
                "clipStart": 10.0, "clipEnd": 20.0, "error": None, "notes": [], "workingEnd": None}


def _remix_record(job_ids: list[str]) -> dict[str, Any]:
    record = _track_record()
    record["kind"] = "remix"
    record["payload"].update({
        "draft": {"clipStart": 10.0, "clipEnd": 20.0, "lyrics": "раз два"},
        "jobIds": job_ids, "masterJobId": job_ids[0], "settings": {},
    })
    return record


def test_remix_link_opens_the_whole_montage_on_the_table(client, monkeypatch) -> None:
    tc, main = client
    billing, backend = _Billing(_remix_record(["bot-a", "bot-b"])), _RemixBackend()
    _production(monkeypatch, main, billing, backend)

    body = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()

    imp = body["wizardImport"]
    assert "wizardImportError" not in body
    assert imp["timing"] == {"from": "00:10:00", "to": "00:20:00"}
    assert imp["timeline"]["cuts"] == [12.0, 15.0]
    assert imp["fxVariants"][0]["kind"] == "effects"
    # превью клипов — подбор «Пула» с клипами бота, закреплёнными на каждом кадре
    assert [v["pins"] for v in backend.picks[0]["videos"]] == [
        {"0": "bot-a-0.mp4", "1": "bot-a-1.mp4", "2": "bot-a-2.mp4"},
        {"0": "bot-b-0.mp4", "1": "bot-b-1.mp4", "2": "bot-b-2.mp4"},
    ]
    assert [c["fileName"] for c in imp["storyboard"][1]["clips"]] == ["bot-b-0.mp4", "bot-b-1.mp4", "bot-b-2.mp4"]
    assert imp["storyboard"][0]["plan"]["clip_start_abs"] == 10.0
    # слова — клон Stage 1 бота (свой на проект), примерка под ключом трек+окно+текст сайта
    assert backend.cloned == ["bot-a"]
    assert backend.clone_keys[0].endswith(f":{body['projectId']}")
    assert imp["asr"]["jobId"] == "asr-clone-1" and imp["asr"]["status"] == "COMPLETED"
    _as_user(main)
    key = main.asr_preview.preview_key(body["track"]["s3Key"], 10.0, 20.0, "раз два")
    assert imp["asr"]["key"] == key and main.store.get_asr_preview()["jobId"] == "asr-clone-1"

    # повтор ссылки монтаж не пересобирает: правки на сайте не затираются
    again = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    assert again["repeat"] is True and "wizardImport" not in again


def test_remix_without_exact_clips_says_so_and_keeps_the_cuts(client, monkeypatch) -> None:
    tc, main = client
    billing, backend = _Billing(_remix_record(["bot-a"])), _RemixBackend(clips_gone=True)
    _production(monkeypatch, main, billing, backend)
    imp = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()["wizardImport"]
    assert imp["storyboard"] == [] and imp["timeline"]["cuts"] == [12.0, 15.0]
    assert any("подберутся заново" in n for n in imp["notes"])


def test_remix_that_cannot_be_imported_falls_back_to_the_draft_with_a_reason(client, monkeypatch) -> None:
    tc, main = client
    backend = _RemixBackend()
    original = backend.job_edit_state

    def other_window(job_id: str) -> dict[str, Any]:
        state = original(job_id)
        if job_id == "bot-b":
            state["window"] = {"clip_start_abs": 30.0, "clip_end_abs": 40.0}
        return state

    backend.job_edit_state = other_window
    _production(monkeypatch, main, _Billing(_remix_record(["bot-a", "bot-b"])), backend)
    body = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    assert "wizardImport" not in body
    assert "разные отрывки" in body["wizardImportError"]
    assert body["draft"]["clipEnd"] == 20.0


def test_dev_remix_link_plays_the_flow_in_mock(client, monkeypatch) -> None:
    tc, main = client
    # без dev-ручек ссылка-демо — обычный отказ мока
    assert tc.post("/api/auth/handoff", json={"token": "dev-remix-demo-0001"}).status_code == 503
    monkeypatch.setattr(main, "DEV_TOOLS", True)
    body = tc.post("/api/auth/handoff", json={"token": "dev-remix-demo-0001"}).json()
    imp = body["wizardImport"]
    assert body["mock"] is True and body["redirectTo"].startswith("/app/generate?project=")
    assert imp["timing"] == {"from": "00:10:23", "to": "00:21:99"}
    assert len(imp["storyboard"]) == 2 and imp["asr"]["status"] == "COMPLETED"
    assert imp["frames"] == {"0": "rounded", "1": "rounded"}


def test_second_remix_link_for_the_same_batch_reuses_the_project(client, monkeypatch) -> None:
    """Вторая кнопка «Докрутить на сайте» под тем же батчем — новый токен, но тот же
    проект: второй проект делил бы с первым клон слов ASR."""
    tc, main = client
    billing, backend = _Billing(_remix_record(["bot-a", "bot-b"])), _RemixBackend()
    _production(monkeypatch, main, billing, backend)
    first = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    billing.record = _remix_record(["bot-b", "bot-a"])  # свежий токен, тот же батч

    second = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()

    assert second["projectId"] == first["projectId"] and second["repeat"] is True
    assert "wizardImport" not in second  # правки на сайте не затираются
    assert backend.cloned == ["bot-a"]  # клон слов не пересоздаётся
    _as_user(main)
    assert [p["id"] for p in main.store.ws().projects] == [first["projectId"]]
    # другой батч того же трека — свой проект
    billing.record = _remix_record(["bot-c"])
    third = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    assert third["projectId"] != first["projectId"] and third["repeat"] is False


def test_remix_with_unreachable_edit_state_says_unavailable(client, monkeypatch) -> None:
    """Ручка состояния монтажа не ответила (старый оркестратор, сбой) — это не «ролик не
    собрался»: человек видит, что монтаж сейчас недоступен, визард — по черновику."""
    tc, main = client
    backend = _RemixBackend()

    def gone(job_id: str) -> dict[str, Any]:
        raise RuntimeError("orchestrator /jobs/x/edit_state failed status=404")

    backend.job_edit_state = gone
    _production(monkeypatch, main, _Billing(_remix_record(["bot-a", "bot-b"])), backend)
    body = tc.post("/api/auth/handoff", json={"token": TOKEN}).json()
    assert "wizardImport" not in body
    assert body["wizardImportError"] == main.bot_import.UNAVAILABLE_TEXT
    assert body["draft"]["clipEnd"] == 20.0


def test_failed_open_does_not_burn_a_single_use_link(client, monkeypatch) -> None:
    """Регресс: ссылка гасилась ДО проверки трека в S3 — сбой S3 сжигал одноразовую
    ссылку (/site, напоминания), и повтор упирался в «ссылка устарела»."""
    tc, main = client
    billing = _Billing(_track_record())
    billing.max_redeems = 1

    class _Flaky(_Backend):
        fail = True

        def register_bot_track(self, s3_url: str, *, filename: str) -> dict[str, str]:
            if self.fail:
                raise TimeoutError("s3 head timeout")
            return super().register_bot_track(s3_url, filename=filename)

    backend = _Flaky()
    _production(monkeypatch, main, billing, backend)
    assert tc.post("/api/auth/handoff", json={"token": TOKEN}).status_code == 503
    assert billing.released == 1 and billing.record["redeem_count"] == 0
    backend.fail = False
    ok = tc.post("/api/auth/handoff", json={"token": TOKEN})
    assert ok.status_code == 200, ok.text
    assert billing.released == 1 and billing.record["redeem_count"] == 1
    # удачное открытие засчитано: одноразовая ссылка теперь и правда погашена
    assert tc.post("/api/auth/handoff", json={"token": TOKEN}).status_code == 410


def test_telegram_taken_while_linking_is_409_not_500(client, monkeypatch) -> None:
    """Пока человек думал над «Привязать?», chat_id привязался к другому аккаунту:
    понятный 409, и одноразовая ссылка не сгорает."""
    tc, main = client
    billing = _Billing(_track_record())
    _production(monkeypatch, main, billing)
    _google_session(tc, main, billing)
    original = billing.redeem_handoff

    async def redeem_with_race(token: str):
        main.auth_store.create_user_from_telegram(CHAT2, {"username": "other"})
        return await original(token)

    billing.redeem_handoff = redeem_with_race
    redeemed = billing.record["redeem_count"]

    r = tc.post("/api/auth/handoff", json={"token": TOKEN, "link": True})

    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "telegram_taken"
    assert billing.record["redeem_count"] == redeemed and billing.released == 1


def test_two_links_for_one_track_opened_at_once_make_one_project(client, monkeypatch) -> None:
    """Регресс: замок был по токену — две РАЗНЫЕ ссылки на один трек, открытые разом
    (напоминание + /site), обе проходили проверку «проекта нет» и заводили по проекту."""
    import asyncio

    tc, main = client
    billing = _Billing(_track_record())
    _production(monkeypatch, main, billing, _Backend())
    tc.post("/api/auth/handoff", json={"token": TOKEN, "force": True})  # аккаунт заведён
    user = main.auth_store.get_user_by_chat(CHAT)
    main.store.use_user(user["id"])
    for project in list(main.store.ws().projects):
        main.store.ws().projects.remove(project)

    gate = asyncio.Event()
    entered = 0

    async def slow_can_upload(tg_id: int, audio_hash: str) -> bool:
        nonlocal entered
        entered += 1
        await gate.wait()  # оба запроса стоят между проверкой и созданием
        return True

    billing.can_upload_track = slow_can_upload

    async def both() -> list[dict[str, Any]]:
        records = [{**_track_record(), "result": {}} for _ in range(2)]
        tasks = [asyncio.create_task(main._handoff_track_project(f"token-{i}", r, CHAT)) for i, r in enumerate(records)]
        await asyncio.sleep(0.05)
        gate.set()
        return list(await asyncio.gather(*tasks))

    first, second = asyncio.run(both())

    assert entered == 1  # второй ждал замка и нашёл проект первого
    assert first["projectId"] == second["projectId"] and second["repeat"] is True
    assert [p["id"] for p in main.store.ws().projects] == [first["projectId"]]
    assert main._HANDOFF_LOCKS == {}


def test_remix_edit_states_are_fetched_in_parallel_with_a_deadline(client, monkeypatch) -> None:
    """Регресс: до 20 последовательных edit_state держали запрос входа. Теперь они
    параллельны, а не успевший к сроку ролик явно помечен «недоступен»."""
    import threading
    import time

    tc, main = client
    backend = _RemixBackend()
    original = backend.job_edit_state
    active = peak = 0
    lock = threading.Lock()
    release = threading.Event()

    def slow(job_id: str) -> dict[str, Any]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            if job_id == "bot-stuck":
                release.wait(5)  # завис дольше срока
            else:
                time.sleep(0.1)
            return original(job_id)
        finally:
            with lock:
                active -= 1

    backend.job_edit_state = slow
    monkeypatch.setattr(main, "_production_backend", lambda: backend)
    monkeypatch.setattr(main, "REMIX_EDIT_STATE_DEADLINE_S", 0.5)
    started = time.monotonic()
    states = main._remix_edit_states(["bot-a", "bot-b", "bot-c", "bot-stuck"])
    elapsed = time.monotonic() - started
    release.set()

    assert peak >= 3 and elapsed < 2.0
    assert [s["status"] for s in states] == ["SUCCEEDED"] * 3 + [main.bot_import.STATUS_UNAVAILABLE]
    assert [s["job_id"] for s in states] == ["bot-a", "bot-b", "bot-c", "bot-stuck"]


def test_remix_import_over_the_total_deadline_says_so(client, monkeypatch) -> None:
    import asyncio

    tc, main = client
    monkeypatch.setattr(main, "REMIX_IMPORT_DEADLINE_S", 0.05)

    async def hang(payload, project):
        await asyncio.sleep(5)

    monkeypatch.setattr(main, "_remix_wizard_import", hang)
    out = asyncio.run(main._remix_import_or_error({"jobIds": ["bot-a"]}, {"projectId": "p"}))
    assert out == {"wizardImportError": main.REMIX_IMPORT_TIMEOUT_TEXT}
