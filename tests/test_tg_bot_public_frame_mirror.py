# -*- coding: utf-8 -*-
"""Parity: шаг «Рамка» одинаково устроен в tg_bot_public и tg_bot_botapi.

Рамка — не хук: она НЕ входит в HOOK_STAGES и не гейтится HOOK_FLOW_ENABLED,
шаг задаётся всем перед выбором версий. Тест держит зеркало id-сета, поля
состояния, kwarg'а клиента и точку перехвата в `_ask_versions` — по правилу
CI-parity правки в обоих `app.py` обязаны иметь тест здесь.
"""
from __future__ import annotations

import inspect
import typing


def test_frame_stage_exists_and_is_not_a_hook_stage():
    from services.tg_bot_public import app as pub
    from services.tg_bot_public.state_store import STAGE_WAIT_FRAME

    assert STAGE_WAIT_FRAME == "WAIT_FRAME"
    # рамка доступна на любом пути => она НЕ часть хук-флоу
    assert STAGE_WAIT_FRAME not in pub.HOOK_STAGES


def test_frame_id_sets_mirror_each_other():
    from services.tg_bot_public import app as pub
    from services.tg_bot_botapi import app as team

    assert pub.FRAME_IDS == {"rounded", "soft_bars", "letterbox"}
    assert set(team.FRAME_IDS) == pub.FRAME_IDS
    # кнопки обоих ботов ведут в один id-сет (+ сентинел отказа)
    assert set(pub._FRAME_BY_BUTTON.values()) == pub.FRAME_IDS | {"none"}
    assert set(team._FRAME_BY_BUTTON.values()) == pub.FRAME_IDS | {"none"}


def test_frame_ids_match_orchestrator_schema():
    """Дрифт id-сета vs schemas.frame_id Literal = тихий отказ оркестратора."""
    from services.tg_bot_public import app as pub
    from services.orchestrator.schemas import SendAudioS3Request

    ann = SendAudioS3Request.model_fields["frame_id"].annotation
    schema_ids: set[str] = set()
    for arg in typing.get_args(ann):
        if arg is type(None):
            continue
        schema_ids.update(typing.get_args(arg))
    assert schema_ids == pub.FRAME_IDS


def test_frame_ids_match_the_asset_catalog():
    from services.tg_bot_public import app as pub
    from mlcore.hooks.frames.catalog import FRAME_IDS as CATALOG_IDS

    assert set(CATALOG_IDS) == pub.FRAME_IDS


def test_chatstate_has_frame_field_defaulting_empty():
    from services.tg_bot_public.state_store import ChatState as PubState
    from services.tg_bot_botapi.state_store import ChatState as TeamState

    assert PubState(chat_id=1).frame_id == ""
    assert TeamState(chat_id=1).frame_id == ""


def test_orchestrator_client_accepts_frame_kwarg():
    from services.tg_bot_public.orchestrator_client import OrchestratorClient as Pub
    from services.tg_bot_botapi.orchestrator_client import OrchestratorClient as Team

    assert "frame_id" in inspect.signature(Pub.send_audio_s3).parameters
    assert "frame_id" in inspect.signature(Team.send_audio_s3).parameters


def test_frame_flow_flag_defaults_team_on_public_off(monkeypatch):
    """Тот же паттерн, что у photo/vibe: team-бот ведёт, public включается
    после заливки ассетов и смоука. Флаг нужен именно потому, что без ассета
    на S3 шаг в боте есть, а рамки в ролике нет.

    Дефолт public читаем из исходника, а не через importlib.reload: перезагрузка
    модуля бота посреди сюиты подменяет объекты, на которые уже держат ссылки
    другие тесты (ловилось падением только в полном прогоне).
    """
    from pathlib import Path

    from services.tg_bot_botapi import app as team

    # team: читает env на каждом вызове — проверяем поведением
    monkeypatch.delenv("FRAME_FLOW_ENABLED", raising=False)
    assert team._frame_flow_enabled() is True
    monkeypatch.setenv("FRAME_FLOW_ENABLED", "0")
    assert team._frame_flow_enabled() is False

    # public: константа уровня модуля — проверяем объявленный дефолт
    pub_src = (Path(__file__).resolve().parents[1]
               / "services" / "tg_bot_public" / "app.py").read_text(encoding="utf-8")
    assert 'FRAME_FLOW_ENABLED = (os.environ.get("FRAME_FLOW_ENABLED", "0")' in pub_src


def test_versions_step_is_gated_on_the_frame_pick_in_both_bots():
    """Перехват стоит внутри `_ask_versions`, потому что в неё ведут все ветки
    флоу; проверяем, что ни один бот его не потерял."""
    from services.tg_bot_public import app as pub
    from services.tg_bot_botapi import app as team

    for mod in (pub, team):
        bot_cls = next(
            obj for _, obj in vars(mod).items()
            if inspect.isclass(obj) and hasattr(obj, "_ask_versions") and hasattr(obj, "_ask_frame")
        )
        src = inspect.getsource(bot_cls._ask_versions)
        assert "st.frame_id" in src and "_ask_frame" in src
        # и шаг обязан быть выключаемым без отката кода
        assert "FRAME_FLOW_ENABLED" in src or "_frame_flow_enabled" in src
        # и сам шаг умеет принять отказ
        handler = inspect.getsource(bot_cls._handle_wait_frame)
        assert "none" in handler
