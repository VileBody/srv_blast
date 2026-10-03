# -*- coding: utf-8 -*-
"""Гейт подписки на канал в публичном боте: включён на онбординге по умолчанию и
fail-closed при сбое getChatMember (решение продукта 2026-10)."""
from __future__ import annotations

from services.tg_bot_public import app as pub
from services.tg_bot_public import marketing_texts as mt
from services.tg_bot_public.state_store import ChatState
from tests.test_tg_bot_public_web_handoff import CHAT, _make_app, _Msg, _run


def _with_check(monkeypatch, app, value):
    async def _check(user_id):
        return value

    monkeypatch.setattr(app, "_check_subscription", _check, raising=False)


def test_onboarding_subscription_is_off_by_default() -> None:
    # Дефолт поля считается при импорте из env — сверяем сам дефолт в коде и в compose.
    from pathlib import Path

    from services.tg_bot_public import config

    # продукт: «Едем!» сразу ведёт к треку, онбординг-гейта по умолчанию нет
    assert '_bool_env("ONBOARDING_SUBSCRIPTION_REQUIRED", False)' in Path(config.__file__).read_text(encoding="utf-8")
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${ONBOARDING_SUBSCRIPTION_REQUIRED:-1}" not in compose


def test_onboarding_gate_can_be_switched_on_explicitly(monkeypatch) -> None:
    from services.tg_bot_public import config

    monkeypatch.setenv("ONBOARDING_SUBSCRIPTION_REQUIRED", "1")
    assert config._bool_env("ONBOARDING_SUBSCRIPTION_REQUIRED", False) is True


def test_onboarding_check_failure_grants_nothing_and_asks_to_retry(monkeypatch) -> None:
    app = _make_app(onboarding_subscription_required=True)
    _with_check(monkeypatch, app, None)
    msg = _Msg(text=pub.BTN_SUBSCRIBED)
    _run(app._handle_wait_subscription(msg, ChatState(chat_id=CHAT)))
    # стартовые кредиты без подтверждённой подписки не выдаём
    assert app.credits_db.grants == []
    assert msg.answers[-1][0] == mt.SUBSCRIPTION_CHECK_UNAVAILABLE
    assert (CHAT, "subscription_check_failed") in app.credits_db.events


def test_onboarding_not_subscribed_grants_nothing(monkeypatch) -> None:
    app = _make_app(onboarding_subscription_required=True)
    _with_check(monkeypatch, app, False)
    moved: list[int] = []

    async def _subscription(chat_id, message):
        moved.append(chat_id)

    monkeypatch.setattr(app, "_move_to_subscription", _subscription, raising=False)
    _run(app._handle_wait_subscription(_Msg(text=pub.BTN_SUBSCRIBED), ChatState(chat_id=CHAT)))
    assert app.credits_db.grants == [] and moved == [CHAT]


def test_before_generation_check_failure_does_not_launch(monkeypatch) -> None:
    app = _make_app(generation_subscription_required=True)
    app.store.by_id[CHAT] = ChatState(chat_id=CHAT, stage=pub.STAGE_WAIT_GEN_SUBSCRIPTION)
    _with_check(monkeypatch, app, None)
    launched: list[bool] = []

    async def _confirm(message, st, *, subscribed=False):
        launched.append(subscribed)

    monkeypatch.setattr(app, "_handle_wait_confirm", _confirm, raising=False)
    msg = _Msg(text=pub.BTN_SUBSCRIBED)
    _run(app._handle_wait_gen_subscription(msg, app.store.by_id[CHAT]))
    assert launched == []
    assert msg.answers[-1][0] == mt.SUBSCRIPTION_CHECK_UNAVAILABLE
    # остаёмся на шаге подписки: следующее «Я подписался» — повтор проверки
    assert app.store.by_id[CHAT].stage == pub.STAGE_WAIT_GEN_SUBSCRIPTION


def test_gate_failure_is_counted(monkeypatch) -> None:
    app = _make_app()
    _with_check(monkeypatch, app, None)
    before = pub.SUBSCRIPTION_CHECK_FAILURES["onboarding"]
    assert _run(app._subscription_gate_passes(CHAT, CHAT, where="onboarding")) is None
    assert pub.SUBSCRIPTION_CHECK_FAILURES["onboarding"] == before + 1
