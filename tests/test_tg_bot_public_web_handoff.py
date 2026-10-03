# -*- coding: utf-8 -*-
"""Публичный бот → сайт: развилка после трека, /site, онбординг без подписки.

docs/BOT_TO_WEB_FLOW.md. Хэндлеры гоняются на in-process фейках (как в
test_tg_bot_public_vibe_flow): сеть, S3 и Postgres подменены.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.tg_bot_public import app as pub
from services.tg_bot_public import marketing_texts as mt
from services.tg_bot_public.credits_db import CreditsDB, WEB_HANDOFF_KINDS, _jsonb_dict
from services.tg_bot_public.state_store import (
    STAGE_WAIT_TIMING_INPUT,
    STAGE_WAIT_WEB_FORK,
    ChatState,
)

CHAT = 7


class _Store:
    def __init__(self):
        self.by_id: dict[int, ChatState] = {}

    async def get(self, chat_id):
        return self.by_id.get(int(chat_id)) or ChatState(chat_id=int(chat_id))

    async def set(self, st):
        self.by_id[int(st.chat_id)] = st


class _CreditsDB:
    def __init__(self, *, paid: bool = False):
        self.events: list[tuple[int, str]] = []
        self.handoffs: list[dict] = []
        self.grants: list[tuple] = []
        self.paid = paid

    async def has_paid(self, tg_id):
        return self.paid

    async def log_event(self, tg_id, event, detail=""):
        self.events.append((tg_id, event))

    async def create_web_handoff(self, tg_id, kind, payload=None, *, ttl_seconds):
        self.handoffs.append({"tg_id": tg_id, "kind": kind, "payload": payload, "ttl": ttl_seconds})
        return f"tok{len(self.handoffs)}"

    async def grant_initial_credits_once(self, tg_id, credits, track_credits, *, actor=""):
        self.grants.append((tg_id, credits, track_credits, actor))
        return {"applied": True}


class _S3:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.uploads: list[dict] = []

    def upload_file(self, *, path, bucket, key, content_type=None):
        if self.fail:
            raise RuntimeError("s3 down")
        self.uploads.append({"path": Path(path), "bucket": bucket, "key": key})
        return f"s3://{bucket}/{key}"


class _Msg:
    def __init__(self, text=""):
        self.text = text
        self.chat = SimpleNamespace(id=CHAT)
        self.from_user = SimpleNamespace(id=CHAT, first_name="Лена", last_name="", username="lena_beats")
        self.audio = self.voice = self.document = None
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None, **kw):
        self.answers.append((text, reply_markup))


def _settings(**over):
    base = dict(
        web_app_url="https://app.blast808.com",
        web_handoff_ttl_s=172800,
        web_handoff_enabled=True,
        onboarding_subscription_required=False,
        s3_bucket_raw_audio="raw-audio",
        s3_raw_audio_prefix="raw_audio",
        initial_credits=5,
        initial_track_credits=1,
        generation_subscription_required=False,
        subscription_channel="@impulsemarketing",
        tg_force_free_funnel_chat_ids=frozenset(),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _make_app(*, s3_fail=False, paid=False, **settings):
    app = pub.BlastBotApp.__new__(pub.BlastBotApp)
    app.store = _Store()
    app.credits_db = _CreditsDB(paid=paid)
    app.s3 = _S3(fail=s3_fail)
    app.settings = _settings(**settings)
    return app


def _state_with_track(tmp_path: Path) -> ChatState:
    audio = tmp_path / "song_prepared.mp3"
    audio.write_bytes(b"ID3 prepared bytes")
    return ChatState(chat_id=CHAT, prepared_audio_local_path=str(audio), pending_audio_filename="My_Song.mp3")


def _run(coro):
    return asyncio.run(coro)


def test_fork_uploads_track_and_offers_site_link(tmp_path):
    app = _make_app()
    st = _state_with_track(tmp_path)
    msg = _Msg()

    _run(app._offer_web_fork(msg, st))

    upload = app.s3.uploads[0]
    assert upload["bucket"] == "raw-audio" and upload["key"].startswith("raw_audio/7/")
    handoff = app.credits_db.handoffs[0]
    assert handoff["kind"] == "track" and handoff["ttl"] == 172800
    payload = handoff["payload"]
    assert payload["audioS3Url"] == f"s3://raw-audio/{upload['key']}"
    # хэш — тот же, по которому бот считает треки (подготовленный mp3)
    assert payload["audioHash"] == hashlib.sha256(b"ID3 prepared bytes").hexdigest()
    assert payload["filename"] == "My_Song.mp3"
    assert payload["profile"] == {"name": "Лена", "surname": "", "username": "lena_beats"}
    saved = app.store.by_id[CHAT]
    assert saved.stage == STAGE_WAIT_WEB_FORK
    assert saved.web_handoff_url == "https://app.blast808.com/go#t=tok1"
    text, markup = msg.answers[-1]
    assert text == mt.WEB_FORK_TEXT
    site_btn, bot_btn = markup.inline_keyboard[0][0], markup.inline_keyboard[1][0]
    assert site_btn.text == mt.BTN_WEB_FORK_SITE and site_btn.url == saved.web_handoff_url
    assert bot_btn.text == mt.BTN_WEB_FORK_BOT and bot_btn.callback_data == mt.WEB_FORK_CALLBACK_BOT
    assert (CHAT, "web_fork_shown") in app.credits_db.events


def test_fork_failure_is_reported_and_continues_in_bot(tmp_path):
    app = _make_app(s3_fail=True)
    msg = _Msg()

    _run(app._offer_web_fork(msg, _state_with_track(tmp_path)))

    assert (CHAT, "web_fork_failed") in app.credits_db.events
    assert app.credits_db.handoffs == []
    assert msg.answers[0][0] == mt.WEB_FORK_UNAVAILABLE
    assert app.store.by_id[CHAT].stage == STAGE_WAIT_TIMING_INPUT


def test_typing_at_fork_sends_a_fresh_single_use_link(tmp_path):
    """Ссылка одноразовая, а открыта ли прежняя, бот не знает — повтор = новая ссылка."""
    app = _make_app()
    st = _state_with_track(tmp_path)
    _run(app._offer_web_fork(_Msg(), st))
    msg = _Msg(text="а что дальше?")

    _run(app._handle_wait_web_fork(msg, app.store.by_id[CHAT]))

    text, markup = msg.answers[-1]
    assert text == mt.WEB_FORK_REMINDER
    assert markup.inline_keyboard[0][0].url == "https://app.blast808.com/go#t=tok2"
    assert app.store.by_id[CHAT].web_handoff_url == "https://app.blast808.com/go#t=tok2"
    assert app.credits_db.handoffs[1]["payload"]["source"] == "refresh"


def test_choosing_bot_continues_the_current_flow(tmp_path):
    app = _make_app()
    _run(app._offer_web_fork(_Msg(), _state_with_track(tmp_path)))
    msg = _Msg(text=mt.BTN_WEB_FORK_BOT)

    _run(app._handle_wait_web_fork(msg, app.store.by_id[CHAT]))

    assert app.store.by_id[CHAT].stage == STAGE_WAIT_TIMING_INPUT
    assert msg.answers[-1][0].startswith(mt.WEB_FORK_BOT_PREFIX)
    assert (CHAT, "web_fork_bot") in app.credits_db.events


def test_lets_go_skips_subscription_and_grants_quota(monkeypatch):
    app = _make_app()
    moved: list[int] = []

    async def _wait_audio(chat_id, message):
        moved.append(chat_id)

    async def _subscription(chat_id, message):
        raise AssertionError("subscription gate must be skipped")

    monkeypatch.setattr(app, "_move_to_wait_audio", _wait_audio, raising=False)
    monkeypatch.setattr(app, "_move_to_subscription", _subscription, raising=False)

    _run(app._handle_wait_start(_Msg(text=pub.BTN_LETS_GO), ChatState(chat_id=CHAT)))

    assert moved == [CHAT]
    assert app.credits_db.grants == [(CHAT, 5, 1, "tg_bot_public")]


def test_subscription_gate_stays_available_behind_the_flag(monkeypatch):
    app = _make_app(onboarding_subscription_required=True)
    gated: list[int] = []

    async def _subscription(chat_id, message):
        gated.append(chat_id)

    monkeypatch.setattr(app, "_move_to_subscription", _subscription, raising=False)
    _run(app._handle_wait_start(_Msg(text=pub.BTN_LETS_GO), ChatState(chat_id=CHAT)))
    assert gated == [CHAT] and app.credits_db.grants == []


def test_chat_parked_on_subscription_is_let_in_when_gate_is_off(monkeypatch):
    app = _make_app()
    moved: list[int] = []

    async def _wait_audio(chat_id, message):
        moved.append(chat_id)

    monkeypatch.setattr(app, "_move_to_wait_audio", _wait_audio, raising=False)
    _run(app._handle_wait_subscription(_Msg(text="привет"), ChatState(chat_id=CHAT)))
    assert moved == [CHAT] and len(app.credits_db.grants) == 1


def test_site_command_sends_a_fresh_login_link():
    app = _make_app()
    msg = _Msg(text="/site")
    _run(app._send_site_link(msg))
    assert app.credits_db.handoffs[0]["kind"] == "site"
    text, markup = msg.answers[0]
    assert text == mt.WEB_SITE_TEXT
    assert markup.inline_keyboard[0][0].url == "https://app.blast808.com/go#t=tok1"


def test_site_command_without_site_says_so():
    app = _make_app(web_app_url="", web_handoff_enabled=False)
    msg = _Msg(text="/site")
    _run(app._send_site_link(msg))
    assert msg.answers == [(mt.WEB_SITE_DISABLED, None)] and app.credits_db.handoffs == []


def test_site_more_lines_only_when_site_is_on(monkeypatch):
    from services.tg_bot_public import config

    monkeypatch.setattr(pub, "SETTINGS", config.Settings(web_app_url="https://app.blast808.com"))
    assert pub._site_more_line(mt.SITE_MORE_STYLES) == "\n\n" + mt.SITE_MORE_STYLES
    monkeypatch.setattr(pub, "SETTINGS", config.Settings(web_app_url=""))
    assert pub._site_more_line(mt.SITE_MORE_STYLES) == ""


def test_audio_step_routes_to_fork_when_site_is_on():
    import inspect

    src = inspect.getsource(pub.BlastBotApp._handle_wait_audio)
    assert "if self.settings.web_handoff_enabled:" in src
    assert "await self._offer_web_fork(message, st)" in src


def test_handoff_kinds_and_token_hash():
    assert WEB_HANDOFF_KINDS == ("track", "remix", "site")
    assert CreditsDB.hash_handoff_token("abc") == hashlib.sha256(b"abc").hexdigest()
    with pytest.raises(ValueError):
        _run(CreditsDB.__new__(CreditsDB).create_web_handoff(1, "job", {}, ttl_seconds=60))
    assert _jsonb_dict('{"a": 1}') == {"a": 1} and _jsonb_dict(None) == {} and _jsonb_dict({"b": 2}) == {"b": 2}


def test_settings_reject_relative_site_url(monkeypatch):
    from services.tg_bot_public import config

    with pytest.raises(RuntimeError):
        config.Settings(web_app_url="app.blast808.com")
    assert config.Settings(web_app_url="https://app.blast808.com").web_handoff_enabled is True
    assert config.Settings(web_app_url="").web_handoff_enabled is False


def test_build_in_bot_reuses_the_fork_upload(tmp_path):
    """«Собрать в боте» после развилки: тот же файл не заливаем и не хэшируем второй раз."""
    app = _make_app()
    st = _state_with_track(tmp_path)
    _run(app._offer_web_fork(_Msg(), st))
    saved = app.store.by_id[CHAT]
    reused = app._fork_upload_for(saved, Path(saved.prepared_audio_local_path))
    assert reused == (saved.web_handoff_audio_s3_url, hashlib.sha256(b"ID3 prepared bytes").hexdigest())
    # переподготовленный файл (другая подпись) — уже не тот трек, зальём заново
    Path(saved.prepared_audio_local_path).write_bytes(b"different bytes!")
    assert app._fork_upload_for(saved, Path(saved.prepared_audio_local_path)) is None


def test_stale_fork_link_is_reissued(tmp_path):
    app = _make_app()
    _run(app._offer_web_fork(_Msg(), _state_with_track(tmp_path)))
    st = app.store.by_id[CHAT]
    st.web_handoff_expires_at = 0.0  # ссылка протухла, пока развилка ждала
    msg = _Msg(text="привет")
    _run(app._handle_wait_web_fork(msg, st))
    assert len(app.credits_db.handoffs) == 2
    assert msg.answers[-1][1].inline_keyboard[0][0].url == "https://app.blast808.com/go#t=tok2"
    assert app.credits_db.handoffs[1]["payload"]["audioS3Url"] == st.web_handoff_audio_s3_url


def test_bot_builds_one_video_and_points_to_the_site_for_batches(monkeypatch):
    app = _make_app()
    shown: list[int] = []

    async def _confirm(message, st):
        shown.append(st.versions_count)

    monkeypatch.setattr(app, "_show_final_confirm_after_versions", _confirm, raising=False)
    monkeypatch.setattr(pub, "FRAME_FLOW_ENABLED", False)
    msg = _Msg()
    st = ChatState(chat_id=CHAT, versions_count=4)
    _run(app._ask_versions(msg, st))
    assert shown == [1] and msg.answers[0][0] == mt.BOT_ONE_VIDEO_NOTE


def test_launch_waits_for_channel_subscription(monkeypatch):
    app = _make_app(generation_subscription_required=True, subscription_channel="@impulsemarketing")
    subscribed = {"ok": False}

    async def _check(user_id):
        return subscribed["ok"]

    monkeypatch.setattr(app, "_check_subscription", _check, raising=False)
    launched: list[bool] = []

    async def _confirm(message, st, *, subscribed=False):
        launched.append(subscribed)

    msg = _Msg(text=pub.BTN_LAUNCH)
    st = ChatState(chat_id=CHAT)
    _run(pub.BlastBotApp._handle_wait_confirm(app, msg, st))
    assert app.store.by_id[CHAT].stage == pub.STAGE_WAIT_GEN_SUBSCRIPTION
    assert "@impulsemarketing" in msg.answers[-1][0]

    monkeypatch.setattr(app, "_handle_wait_confirm", _confirm, raising=False)
    not_yet = _Msg(text=pub.BTN_SUBSCRIBED)
    _run(app._handle_wait_gen_subscription(not_yet, app.store.by_id[CHAT]))
    assert launched == [] and "Пока не видим" in not_yet.answers[-1][0]
    subscribed["ok"] = True
    _run(app._handle_wait_gen_subscription(_Msg(text=pub.BTN_SUBSCRIBED), app.store.by_id[CHAT]))
    assert launched == [True]


def test_after_pitch_and_low_rating_the_bot_leads_to_the_site_not_to_a_friend_invite():
    app = _make_app()
    st = ChatState(chat_id=CHAT, web_remix_payload={"audioS3Url": "s3://raw/x.mp3", "audioHash": "h", "draft": {"clipStart": 1.0, "clipEnd": 9.0, "lyrics": "l"}})
    msg = _Msg()
    _run(app._show_referral_ask(msg, st))
    assert msg.answers[-1][0] == mt.SITE_CTA_AFTER_PITCH
    assert app.credits_db.handoffs[-1]["kind"] == "remix"
    assert app.store.by_id[CHAT].stage == pub.STAGE_IDLE
    low = _Msg(text=pub.BTN_RATE_LOW)
    _run(app._handle_rate_video(low, ChatState(chat_id=CHAT)))
    assert low.answers[-1][0] == mt.SITE_CTA_AFTER_LOW
    assert app.credits_db.handoffs[-1]["kind"] == "site"  # трек неизвестен — просто в аккаунт


class _Callback:
    def __init__(self):
        self.message = _Msg()
        self.answers: list[str | None] = []

    async def answer(self, text=None, **kw):
        self.answers.append(text)
        await asyncio.sleep(0)  # отдаём управление: второе нажатие приходит «посреди» первого


def test_double_tap_build_in_bot_runs_the_flow_once(tmp_path):
    """Два нажатия «Собрать в боте» подряд: флоу бота запускается один раз."""
    app = _make_app()
    _run(app._offer_web_fork(_Msg(), _state_with_track(tmp_path)))
    first, second = _Callback(), _Callback()

    async def both():
        await asyncio.gather(app._handle_web_fork_bot_callback(first), app._handle_web_fork_bot_callback(second))

    _run(both())

    assert [e for e in app.credits_db.events if e[1] == "web_fork_bot"] == [(CHAT, "web_fork_bot")]
    assert "Уже выбрано." in first.answers + second.answers
    assert app.store.by_id[CHAT].stage == STAGE_WAIT_TIMING_INPUT


def test_refreshed_fork_link_does_not_restart_reminders(tmp_path, monkeypatch):
    """Ссылка развилки помечена `fork`, перевыпуск протухшей — `refresh`."""
    app = _make_app()
    _run(app._offer_web_fork(_Msg(), _state_with_track(tmp_path)))
    st = app.store.by_id[CHAT]
    st.web_handoff_expires_at = 0  # ссылка протухла
    _run(app._handle_wait_web_fork(_Msg(text="ау"), st))
    assert [h["payload"]["source"] for h in app.credits_db.handoffs] == ["fork", "refresh"]


class _RecordingConn:
    def __init__(self):
        self.sql: list[str] = []

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        return []


class _RecordingPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def test_reminder_rows_anchor_on_the_fork_token_and_compare_in_one_timezone():
    """Выборка держит цепочку на токене развилки: токены напоминаний (source=reminder)
    её не перезапускают, а «открыл» — любой погашенный track-токен того же трека."""
    conn = _RecordingConn()
    db = CreditsDB.__new__(CreditsDB)
    db._pool_or_fail = lambda: _RecordingPool(conn)
    _run(db.site_handoff_reminder_rows(chain_days=4, lookback_days=10))
    _run(db.idle_generation_rows())
    reminders, idle = conn.sql
    assert "COALESCE(t.payload->>'source', 'fork') = 'fork'" in reminders
    assert "SUM(r.redeem_count)" in reminders and "r.payload->>'audioHash'" in reminders
    # web_activity_log — наивное UTC, activity_log — время сессии: обе к TIMESTAMPTZ
    assert "w.created_at AT TIME ZONE 'UTC'" in reminders
    assert "created_at AT TIME ZONE 'UTC' AS created_at FROM web_activity_log" in idle
    assert "created_at::timestamptz AS created_at FROM activity_log" in idle


def test_remix_link_carries_batch_jobs_and_settings_snapshot(tmp_path):
    """«Докрутить на сайте»: ссылка несёт ролики батча (сайт откроет их монтаж на столе)
    и снимок выбора в боте — снят ДО сброса состояния батча."""
    app = _make_app()
    st = _state_with_track(tmp_path)
    st.batch_audio_s3_url = "s3://raw-audio/raw_audio/7/x.mp3"
    st.user_clip_start_sec, st.user_clip_end_sec, st.target_fragment = 41.5, 56.25, "строка"
    st.job_order, st.master_job_id = ["job-a", "job-b"], "job-a"
    st.subtitles_mode, st.visual_transition, st.visual_style = "brat_5th", "minimax", "xerox"
    st.hook_enabled, st.hook_category, st.f2_shape, st.hook_drop_t = True, "object", "rhomb", 50.0
    st.frame_id, st.vibe_selected_ids, st.accent_color_hex = "rounded", ["visual:night"], "#ff0000"

    source = app._site_remix_source(st)
    app._reset_processing_state(st)  # как в боевом пути: сброс сразу после снимка
    sent: list = []
    bot = SimpleNamespace(send_message=lambda *a, **kw: _async_append(sent, (a, kw)))
    _run(app._offer_site_remix_best_effort(bot=bot, st=st, source=source))

    payload = app.credits_db.handoffs[-1]["payload"]
    assert app.credits_db.handoffs[-1]["kind"] == "remix"
    assert payload["jobIds"] == ["job-a", "job-b"] and payload["masterJobId"] == "job-a"
    snap = payload["settings"]
    assert snap["subtitlesMode"] == "brat_5th" and snap["visualTransition"] == "minimax"
    assert snap["hookCategory"] == "object" and snap["f2Shape"] == "rhomb" and snap["hookDropT"] == 50.0
    assert snap["frameId"] == "rounded" and snap["vibeSelectedIds"] == ["visual:night"]
    assert payload["draft"] == {"clipStart": 41.5, "clipEnd": 56.25, "lyrics": "строка"}
    # та же ссылка переиспользуется после оценки ролика
    assert st.web_remix_payload["jobIds"] == ["job-a", "job-b"]
    assert sent


async def _async_append(bucket, item):
    bucket.append(item)


# ── ревью: ограничения бота — только бесплатным, сбой проверки подписки, двойной тап ──

def test_paid_users_get_the_version_picker_not_the_one_video_note(monkeypatch):
    """«Один ролик за раз» — только бесплатным; платящий собирает как раньше."""
    app = _make_app(paid=True)
    monkeypatch.setattr(pub, "FRAME_FLOW_ENABLED", False)
    msg = _Msg()
    st = ChatState(chat_id=CHAT, versions_count=4)
    _run(app._ask_versions(msg, st))
    assert msg.answers[0][0] != mt.BOT_ONE_VIDEO_NOTE
    assert app.store.by_id[CHAT].stage == pub.STAGE_WAIT_VERSIONS


def test_paid_users_launch_without_the_channel_step(monkeypatch):
    app = _make_app(paid=True, generation_subscription_required=True)
    checked: list[int] = []

    async def _check(user_id):
        checked.append(user_id)
        return False

    monkeypatch.setattr(app, "_check_subscription", _check, raising=False)
    monkeypatch.setattr(app, "_has_timing_window", lambda st: False, raising=False)
    msg = _Msg(text=pub.BTN_LAUNCH)
    _run(pub.BlastBotApp._handle_wait_confirm(app, msg, ChatState(chat_id=CHAT)))
    assert checked == []  # подписку платящего не проверяем вовсе
    assert app.store.by_id[CHAT].stage != pub.STAGE_WAIT_GEN_SUBSCRIPTION


def test_failed_subscription_check_blocks_the_launch_with_a_retry(monkeypatch):
    """getChatMember сломался (бот не админ, флуд) — это не «не подписан», но и не пропуск:
    fail-closed, человеку «не смогли проверить», событие в activity_log. Дальше — повтор
    кнопкой «Я подписался» на шаге подписки."""
    app = _make_app(generation_subscription_required=True)
    result = {"value": None}

    async def _check(user_id):
        return result["value"]

    monkeypatch.setattr(app, "_check_subscription", _check, raising=False)
    monkeypatch.setattr(app, "_has_timing_window", lambda st: False, raising=False)
    msg = _Msg(text=pub.BTN_LAUNCH)
    _run(pub.BlastBotApp._handle_wait_confirm(app, msg, ChatState(chat_id=CHAT)))
    assert app.store.by_id[CHAT].stage == pub.STAGE_WAIT_GEN_SUBSCRIPTION
    assert msg.answers[-1][0] == mt.SUBSCRIPTION_CHECK_UNAVAILABLE
    assert (CHAT, "subscription_check_failed") in app.credits_db.events

    result["value"] = False
    _run(pub.BlastBotApp._handle_wait_confirm(app, _Msg(text=pub.BTN_LAUNCH), ChatState(chat_id=CHAT)))
    assert app.store.by_id[CHAT].stage == pub.STAGE_WAIT_GEN_SUBSCRIPTION


def test_check_subscription_tells_failure_from_not_subscribed():
    app = _make_app()

    class _Bot:
        def __init__(self, status=None, fail=False):
            self.status, self.fail = status, fail

        async def get_chat_member(self, chat_id, user_id):
            if self.fail:
                raise RuntimeError("Bad Request: member list is inaccessible")
            return SimpleNamespace(status=self.status)

    for bot, expected in ((_Bot(status="member"), True), (_Bot(status="left"), False), (_Bot(fail=True), None)):
        app._require_bot = lambda bot=bot: bot
        assert _run(app._check_subscription(CHAT)) is expected


def test_double_tap_subscribed_launches_once(monkeypatch):
    """Два «Я подписался» подряд: генерация запускается один раз (чат занят до первого await)."""
    app = _make_app(generation_subscription_required=True)
    app.store.by_id[CHAT] = ChatState(chat_id=CHAT, stage=pub.STAGE_WAIT_GEN_SUBSCRIPTION)
    gate = asyncio.Event()
    launched: list[bool] = []

    async def _check(user_id):
        await gate.wait()
        return True

    async def _confirm(message, st, *, subscribed=False):
        launched.append(subscribed)
        st.stage = pub.STAGE_PROCESSING
        await app.store.set(st)

    monkeypatch.setattr(app, "_check_subscription", _check, raising=False)
    monkeypatch.setattr(app, "_handle_wait_confirm", _confirm, raising=False)

    async def both():
        first = asyncio.create_task(app._handle_wait_gen_subscription(_Msg(text=pub.BTN_SUBSCRIBED), app.store.by_id[CHAT]))
        second = asyncio.create_task(app._handle_wait_gen_subscription(_Msg(text=pub.BTN_SUBSCRIBED), app.store.by_id[CHAT]))
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(first, second)
        # третий тап уже после запуска — стадия сменилась, второй раз не запускаем
        await app._handle_wait_gen_subscription(_Msg(text=pub.BTN_SUBSCRIBED), app.store.by_id[CHAT])

    _run(both())
    assert launched == [True]


def test_reset_to_wait_audio_forgets_the_remix_payload():
    """Ссылка «на сайт» после оценки не должна вести на ролик прошлого батча."""
    from services.tg_bot_public.state_store import RedisChatStateStore

    store = RedisChatStateStore.__new__(RedisChatStateStore)
    saved = {CHAT: ChatState(chat_id=CHAT, web_remix_payload={"audioS3Url": "s3://raw/old.mp3"})}

    async def _get(chat_id):
        return saved[int(chat_id)]

    async def _set(st):
        saved[int(st.chat_id)] = st

    store.get, store.set = _get, _set
    _run(store.reset_to_wait_audio(CHAT))
    assert saved[CHAT].web_remix_payload == {}


def test_recharge_and_idle_rows_query_only_what_they_need():
    """«Лимиты обновились»: без трека с трипваером (track_tripwire, а не мёртвая колонка),
    только недавно генерировавшие, батчи — тем же запросом. Затихшие — только участники
    воронки бот → сайт. Цепочка развилки живёт и от открытия ссылки."""
    conn = _RecordingConn()
    db = CreditsDB.__new__(CreditsDB)
    db._pool_or_fail = lambda: _RecordingPool(conn)
    _run(db.track_unlimited_rows(active_hours=48))
    _run(db.idle_generation_rows())
    _run(db.site_handoff_reminder_rows(chain_days=4, lookback_days=10))
    recharge, idle, forks = conn.sql
    assert "track_tripwire t WHERE t.tg_id = u.tg_id AND t.audio_hash = u.audio_hash" in recharge
    assert "tripwire_paid_at" not in recharge
    assert "make_interval(hours => $1)" in recharge and "array_agg(b.created_at" in recharge
    for member in ("FROM web_handoff_tokens t", "a.event = 'web_fork_shown'", "FROM track_unlimited u",
                   "f.action = 'tripwire_offer'"):
        assert member in idle, member
    assert "opened.first_open > NOW() - make_interval(days => $1)" in forks


def test_release_web_handoff_returns_one_redemption_never_below_zero():
    """Сайт погасил ссылку, но открыть её не смог — погашение возвращается (одноразовая
    ссылка не сгорает на сбое, а напоминания не считают её «открытой»)."""
    calls: list[tuple] = []

    class _Conn:
        async def execute(self, sql, *args):
            calls.append((sql, args))
            return "UPDATE 1"

    db = CreditsDB.__new__(CreditsDB)
    db._pool_or_fail = lambda: _RecordingPool(_Conn())
    _run(db.release_web_handoff("tok-123"))
    _run(db.release_web_handoff(""))  # пустой токен — ничего не трогаем
    assert len(calls) == 1
    sql, args = calls[0]
    assert "GREATEST(redeem_count - 1, 0)" in sql and args == (CreditsDB.hash_handoff_token("tok-123"),)


def test_failed_subscription_check_is_blocked_counted_and_logged_as_error(monkeypatch, caplog):
    """Сбой проверки — fail-closed (None, не пропуск), и он обязан быть виден:
    error-лог со счётчиком по месту проверки, а не warning."""
    import logging

    app = _make_app(generation_subscription_required=True)

    async def _broken(user_id):
        return None

    monkeypatch.setattr(app, "_check_subscription", _broken, raising=False)
    monkeypatch.setattr(pub, "SUBSCRIPTION_CHECK_FAILURES", pub.collections.Counter())
    with caplog.at_level(logging.ERROR, logger="tg_bot"):
        assert _run(pub.BlastBotApp._subscription_gate_passes(app, 1, CHAT, where="launch")) is None
        assert _run(pub.BlastBotApp._subscription_gate_passes(app, 1, CHAT, where="launch")) is None
    assert pub.SUBSCRIPTION_CHECK_FAILURES["launch"] == 2
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("subscription_check_failed_blocked" in m and "count=2" in m for m in errors)
