# -*- coding: utf-8 -*-
"""Parity test: tg_bot_public mirrors the F6 «Прогрев видео» flow of tg_bot_botapi.

«Прогрев» — одна кнопка меню с двумя рукавами: свой звук (F1) или своя
видео-вырезка (F6). UX живёт в team-боте, публичный зеркалит стадии, поля
ChatState, kwargs клиента и правило подгонки окна под длину вырезки — иначе
CI-гейт parity красный, а фича при включении HOOK_FLOW_ENABLED разъедется.
"""
from __future__ import annotations

import inspect


def test_warmup_and_f6_stages_are_in_hook_stages():
    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import (
        STAGE_WAIT_F6_VIDEO,
        STAGE_WAIT_WARMUP_KIND,
    )

    assert STAGE_WAIT_WARMUP_KIND in pub.HOOK_STAGES
    assert STAGE_WAIT_F6_VIDEO in pub.HOOK_STAGES


def test_stage_values_match_between_bots():
    from services.tg_bot_botapi.state_store import STAGE_WAIT_F6_VIDEO as TEAM_VIDEO
    from services.tg_bot_botapi.state_store import STAGE_WAIT_WARMUP_KIND as TEAM_KIND
    from services.tg_bot_public.state_store import STAGE_WAIT_F6_VIDEO as PUB_VIDEO
    from services.tg_bot_public.state_store import STAGE_WAIT_WARMUP_KIND as PUB_KIND

    assert TEAM_KIND == PUB_KIND == "WAIT_WARMUP_KIND"
    assert TEAM_VIDEO == PUB_VIDEO == "WAIT_F6_VIDEO"


def test_chatstate_has_f6_fields_with_neutral_defaults():
    from services.tg_bot_public.state_store import ChatState

    st = ChatState(chat_id=1)
    assert st.warmup_kind == ""
    assert st.f6_video_url == ""
    assert st.f6_video_width == 0
    assert st.f6_video_height == 0
    assert st.f6_video_duration == 0.0
    # Немой файл — исключение, а не норма: по умолчанию считаем, что звук есть.
    assert st.f6_video_has_audio is True


def test_both_bots_carry_the_same_chatstate_f6_fields():
    from services.tg_bot_botapi.state_store import ChatState as TeamState
    from services.tg_bot_public.state_store import ChatState as PubState

    fields = {
        "warmup_kind",
        "f6_video_url",
        "f6_video_width",
        "f6_video_height",
        "f6_video_duration",
        "f6_video_has_audio",
    }
    assert fields <= set(TeamState.model_fields)
    assert fields <= set(PubState.model_fields)


def test_orchestrator_client_accepts_f6_kwargs():
    from services.tg_bot_public.orchestrator_client import OrchestratorClient

    sig = inspect.signature(OrchestratorClient.send_audio_s3)
    for name in (
        "f6_video_url",
        "f6_video_width",
        "f6_video_height",
        "f6_video_duration",
        "f6_video_has_audio",
    ):
        assert name in sig.parameters, name


def test_schema_accepts_what_the_public_client_sends():
    from services.orchestrator.schemas import SendAudioS3Request

    for name in (
        "f6_video_url",
        "f6_video_width",
        "f6_video_height",
        "f6_video_duration",
        "f6_video_has_audio",
    ):
        assert name in SendAudioS3Request.model_fields, name


def test_warmup_button_labels_match_between_bots():
    from services.tg_bot_botapi import app as team
    from services.tg_bot_public import app as pub

    assert team.BTN_HOOK_CAT_WARMUP == pub.BTN_HOOK_CAT_WARMUP == "Прогрев"
    assert team.BTN_WARMUP_SOUND == pub.BTN_WARMUP_SOUND
    assert team.BTN_WARMUP_VIDEO == pub.BTN_WARMUP_VIDEO
    # «Прогрев» остаётся категорией "sound" — id менять нельзя, на него завязаны
    # гейты enqueue и батарея.
    assert team._HOOK_CATEGORY_BY_BUTTON[team.BTN_HOOK_CAT_WARMUP] == "sound"


def test_public_bot_extracts_the_same_video_shapes():
    from services.tg_bot_botapi import app as team
    from services.tg_bot_public import app as pub

    assert team._VIDEO_EXTS == pub._VIDEO_EXTS

    class _Msg:
        text = None
        audio = None
        document = None
        video = None
        video_note = None
        animation = None

    class _File:
        file_id = "abc"
        file_name = "cut.mp4"

    msg = _Msg()
    msg.video = _File()
    assert team._extract_video_spec(msg) == pub._extract_video_spec(msg) == ("abc", "cut.mp4")

    empty = _Msg()
    assert team._extract_video_spec(empty) is None
    assert pub._extract_video_spec(empty) is None


def test_reset_clears_f6_in_both_bots():
    from services.tg_bot_botapi import app as team
    from services.tg_bot_botapi.state_store import ChatState as TeamState
    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import ChatState as PubState

    for mod, State in ((team, TeamState), (pub, PubState)):
        st = State(chat_id=1)
        st.f6_video_url = "s3://b/cut.mp4"
        st.f6_video_width = 1920
        st.f6_video_height = 1080
        st.f6_video_duration = 4.0
        st.f6_video_has_audio = False
        mod._reset_f6(st)
        assert st.f6_video_url == ""
        assert st.f6_video_width == 0
        assert st.f6_video_height == 0
        assert st.f6_video_duration == 0.0
        assert st.f6_video_has_audio is True


def test_both_bots_reframe_the_clip_onto_the_warm_up_length():
    """Окно подгоняется под вырезку: clip_start = drop − (dur + пады).

    Пады обязаны совпадать с теми, по которым build-сторона ставит слой, иначе
    вырезка не встанет встык к дропу — ровно тот класс рассинхрона, который
    ловили на F4.
    """
    import re

    from mlcore.hooks.f6_video.inject import F6_LEAD_PAD_SEC, F6_TAIL_PAD_SEC

    expected = "float(st.f6_video_duration or 0.0) + F6_LEAD_PAD_SEC + F6_TAIL_PAD_SEC"
    for path in (
        "services/tg_bot_botapi/app.py",
        "services/tg_bot_public/app.py",
    ):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert expected in src, path
        assert re.search(r"lead_f6\s*=", src), path

    assert F6_LEAD_PAD_SEC == F6_TAIL_PAD_SEC == 0.0


# ---- поведение развилки (публичный бот, стаб-приложение) ----

class _Store:
    async def set(self, st):
        self.state = st


class _Message:
    def __init__(self, text=""):
        self.text = text
        self.answers = []

    async def answer(self, text="", reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return self


class _App:
    store = _Store()

    # Разбор таймингов — staticmethod класса бота; на стабе его нет, а хендлер
    # зовёт его через self. Берём настоящий, чтобы тест проверял тот же парсер,
    # что и прод.
    @staticmethod
    def _parse_timing(text):
        from services.tg_bot_public import app as pub

        return pub.BlastBotApp._parse_timing(text)


def _run_fork(button_text):
    import asyncio

    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import ChatState

    async def run():
        app = _App()
        seen = {"sound": 0, "video": 0}

        async def ask_f1_sound(_m, _s):
            seen["sound"] += 1

        async def ask_f6_video(_m, _s):
            seen["video"] += 1

        app._ask_f1_sound = ask_f1_sound
        app._ask_f6_video = ask_f6_video

        st = ChatState(chat_id=1, hook_enabled=True, hook_category="sound", hook_drop_t=6.0)
        msg = _Message(button_text)
        await pub.BlastBotApp._handle_wait_warmup_kind(app, msg, st)
        return seen, st, msg

    return asyncio.run(run())


def test_sound_arm_routes_to_the_f1_upload():
    from services.tg_bot_public import app as pub

    seen, st, _msg = _run_fork(pub.BTN_WARMUP_SOUND)
    assert seen == {"sound": 1, "video": 0}
    assert st.warmup_kind == "sound"
    # Переключение на звук стирает ранее выбранное видео — иначе enqueue
    # отправил бы обе вставки разом.
    assert st.f6_video_url == ""


def test_video_arm_routes_to_the_f6_upload_and_drops_the_sound():
    from services.tg_bot_public import app as pub

    seen, st, _msg = _run_fork(pub.BTN_WARMUP_VIDEO)
    assert seen == {"sound": 0, "video": 1}
    assert st.warmup_kind == "video"
    assert st.f1_sound_url == ""
    assert st.f1_sound_text == ""


def test_both_f6_prompts_render_without_unary_plus_crash():
    import asyncio
    from types import SimpleNamespace

    from services.tg_bot_botapi import app as team
    from services.tg_bot_botapi.state_store import ChatState as TeamState
    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import ChatState as PublicState

    async def run(method, state_cls):
        app = _App()
        app.settings = SimpleNamespace(external_video_source_enabled=False)
        msg = _Message()
        st = state_cls(chat_id=1)
        await method(app, msg, st)
        assert msg.answers
        assert "Прогрев видео" in msg.answers[0][0]

    asyncio.run(run(team.BlastBotApp._ask_f6_video, TeamState))
    asyncio.run(run(pub.BlastBotApp._ask_f6_video, PublicState))


def test_unknown_answer_stays_on_the_fork():
    from services.tg_bot_public import app as pub

    seen, st, msg = _run_fork("что-то своё")
    assert seen == {"sound": 0, "video": 0}
    assert st.warmup_kind == ""
    assert msg.answers, "бот обязан подсказать, а не молчать"


def test_enqueue_sends_only_one_warm_up_arm():
    """Гейты enqueue: видео-рукав глушит f1_sound_url и наоборот."""
    import inspect

    from services.tg_bot_public import app as pub

    src = inspect.getsource(pub.BlastBotApp)
    assert 'st.warmup_kind != "video"' in src
    assert 'st.warmup_kind == "video"' in src


# ---- ветка «ссылка на YouTube» ----

def test_youtube_stage_is_mirrored_and_routed():
    from services.tg_bot_botapi.state_store import STAGE_WAIT_F6_YT_RANGE as TEAM
    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import STAGE_WAIT_F6_YT_RANGE as PUB

    assert TEAM == PUB == "WAIT_F6_YT_RANGE"
    assert PUB in pub.HOOK_STAGES


def test_source_url_field_is_mirrored():
    from services.tg_bot_botapi.state_store import ChatState as TeamState
    from services.tg_bot_public.state_store import ChatState as PubState

    for State in (TeamState, PubState):
        assert "f6_source_url" in State.model_fields
        assert State(chat_id=1).f6_source_url == ""


def test_reset_also_clears_the_source_url():
    from services.tg_bot_botapi import app as team
    from services.tg_bot_botapi.state_store import ChatState as TeamState
    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import ChatState as PubState

    for mod, State in ((team, TeamState), (pub, PubState)):
        st = State(chat_id=1)
        st.f6_source_url = "https://youtu.be/abc"
        mod._reset_f6(st)
        assert st.f6_source_url == ""


def test_both_clients_can_call_the_fetch_endpoint():
    import inspect

    from services.tg_bot_botapi.orchestrator_client import (
        OrchestratorClient as TeamClient,
    )
    from services.tg_bot_public.orchestrator_client import (
        OrchestratorClient as PubClient,
    )

    for Client in (TeamClient, PubClient):
        sig = inspect.signature(Client.fetch_external_video)
        assert {"url", "start_sec", "end_sec"} <= set(sig.parameters)


def test_http_error_keeps_the_status_code():
    """Бот отличает 503 (ветка выключена) от 422 (юзеру есть что поправить) —
    без кода оба превратились бы в одинаковое «что-то пошло не так»."""
    from services.tg_bot_public.orchestrator_client import OrchestratorHTTPError

    err = OrchestratorHTTPError(422, "отрезок слишком длинный")
    assert err.status_code == 422
    assert "слишком длинный" in err.detail


def test_youtube_branch_is_off_by_default_in_both_bots():
    from services.tg_bot_botapi.config import Settings as TeamSettings
    from services.tg_bot_public.config import Settings as PubSettings

    for Settings in (TeamSettings, PubSettings):
        assert Settings().external_video_source_enabled is False


def test_link_step_is_gated_by_the_same_flag_in_both_bots():
    """Один переключатель на бота и оркестратор: иначе бот предлагал бы ссылку,
    а эндпоинт отвечал бы 503."""
    for path in ("services/tg_bot_botapi/app.py", "services/tg_bot_public/app.py"):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        assert "self.settings.external_video_source_enabled" in src, path
        assert "is_supported_url" in src, path


def test_range_handler_rejects_a_window_outside_the_warm_up_limits():
    import asyncio

    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import ChatState

    async def run():
        app = _App()
        called = {"fetch": 0}

        class _Orch:
            async def fetch_external_video(self, **kw):
                called["fetch"] += 1
                return {}

        app.orchestrator = _Orch()
        st = ChatState(chat_id=1, hook_enabled=True, hook_category="sound",
                       hook_drop_t=20.0, warmup_kind="video",
                       f6_source_url="https://youtu.be/abc123XYZ_-")
        msg = _Message("0:00 - 1:30")  # 90с — сильно больше потолка прогрева
        await pub.BlastBotApp._handle_wait_f6_yt_range(app, msg, st)
        return called, msg

    called, msg = asyncio.run(run())
    # Сеть не трогаем: отказ должен случиться до вызова оркестратора.
    assert called["fetch"] == 0
    assert msg.answers


def test_range_handler_fills_state_from_the_orchestrator_answer():
    import asyncio

    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import ChatState

    async def run():
        app = _App()
        seen = {"versions": 0}

        class _Orch:
            async def fetch_external_video(self, *, url, start_sec, end_sec):
                assert url == "https://youtu.be/abc123XYZ_-"
                assert (start_sec, end_sec) == (12.0, 19.0)
                return {
                    "video_url": "s3://raw/external_video/abc.mp4",
                    "width": 1920, "height": 1080,
                    "duration_sec": 7.0, "has_audio": True,
                }

        async def proceed(_m, _s):
            seen["versions"] += 1

        app.orchestrator = _Orch()
        app._proceed_to_versions_or_confirm = proceed
        st = ChatState(chat_id=1, hook_enabled=True, hook_category="sound",
                       hook_drop_t=20.0, warmup_kind="video",
                       f6_source_url="https://youtu.be/abc123XYZ_-")
        await pub.BlastBotApp._handle_wait_f6_yt_range(app, _Message("0:12 - 0:19"), st)
        return seen, st

    seen, st = asyncio.run(run())
    assert st.f6_video_url == "s3://raw/external_video/abc.mp4"
    assert (st.f6_video_width, st.f6_video_height) == (1920, 1080)
    assert st.f6_video_duration == 7.0
    assert seen["versions"] == 1


def test_a_blocked_source_sends_the_user_back_to_the_file_upload():
    """502 «нас забанили» — не повод бросать юзера: предлагаем прислать файлом."""
    import asyncio

    from services.tg_bot_public import app as pub
    from services.tg_bot_public.orchestrator_client import OrchestratorHTTPError
    from services.tg_bot_public.state_store import ChatState

    async def run():
        app = _App()
        seen = {"ask_video": 0}

        class _Orch:
            async def fetch_external_video(self, **kw):
                raise OrchestratorHTTPError(502, "YouTube не отдал видео")

        async def ask_video(_m, _s):
            seen["ask_video"] += 1

        app.orchestrator = _Orch()
        app._ask_f6_video = ask_video
        st = ChatState(chat_id=1, hook_enabled=True, hook_category="sound",
                       hook_drop_t=20.0, warmup_kind="video",
                       f6_source_url="https://youtu.be/abc123XYZ_-")
        await pub.BlastBotApp._handle_wait_f6_yt_range(app, _Message("0:12 - 0:19"), st)
        return seen, st

    seen, st = asyncio.run(run())
    assert seen["ask_video"] == 1
    assert st.f6_video_url == ""


# ---- гейт видео-рукава (team ON / public OFF) ----

def test_video_arm_defaults_on_in_team_and_off_in_public(monkeypatch):
    """Публичный бот показывает хук-флоу (HOOK_FLOW_ENABLED=1 в compose), поэтому
    без отдельного гейта видео-рукав уехал бы к живым юзерам вместе с мёржем."""
    from services.tg_bot_botapi import app as team
    from services.tg_bot_public import app as pub

    monkeypatch.delenv("WARMUP_VIDEO_ENABLED", raising=False)
    assert team._warmup_video_enabled() is True
    assert pub._warmup_video_enabled() is False


def test_the_gate_is_overridable_without_a_deploy(monkeypatch):
    from services.tg_bot_public import app as pub

    monkeypatch.setenv("WARMUP_VIDEO_ENABLED", "1")
    assert pub._warmup_video_enabled() is True
    monkeypatch.setenv("WARMUP_VIDEO_ENABLED", "0")
    assert pub._warmup_video_enabled() is False


def _run_category_pick(monkeypatch, *, enabled):
    import asyncio

    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import ChatState

    monkeypatch.setenv("WARMUP_VIDEO_ENABLED", "1" if enabled else "0")

    async def run():
        app = _App()
        seen = {"fork": 0, "sound": 0}

        async def ask_fork(_m, _s):
            seen["fork"] += 1

        async def ask_sound(_m, _s):
            seen["sound"] += 1

        app._ask_warmup_kind = ask_fork
        app._ask_f1_sound = ask_sound
        st = ChatState(chat_id=1, hook_enabled=True, hook_drop_t=6.0,
                       user_clip_start_sec=0.0)
        await pub.BlastBotApp._handle_wait_hook_type(
            app, _Message(pub.BTN_HOOK_CAT_WARMUP), st,
        )
        return seen, st

    return asyncio.run(run())


def test_gate_off_keeps_the_old_sound_only_behaviour(monkeypatch):
    seen, st = _run_category_pick(monkeypatch, enabled=False)
    assert seen == {"fork": 0, "sound": 1}
    assert st.hook_category == "sound"
    assert st.warmup_kind == "sound"


def test_gate_on_opens_the_fork(monkeypatch):
    seen, st = _run_category_pick(monkeypatch, enabled=True)
    assert seen == {"fork": 1, "sound": 0}
    assert st.warmup_kind == ""
