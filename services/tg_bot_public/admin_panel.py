"""Lightweight admin web panel — runs as a background asyncio task inside the bot process."""

from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
import secrets
import shlex
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote as url_quote, quote_plus
from typing import Any, Dict, Optional, TYPE_CHECKING

from .broadcast_sender import send_bot_message
from . import admin_product_card
from . import product_metrics

import httpx
import uvicorn
from fastapi import FastAPI, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from core.llm_worker_types import LLM_WORKER_TYPES
from services.generation_runtime import GenerationRuntimeStore
from services.orchestrator.alignment_smoke_auth import (
    AUTHORIZATION_TTL_S,
    alignment_smoke_authorization_digest,
    alignment_smoke_authorization_key,
)
from services.orchestrator.windows_node_pool import normalize_windows_urls, runtime_windows_urls_key

from .credits_db import (
    normalize_package_code as _normalize_pkg_code,
    package_video_credits,
)
from .render_node_pool import (
    list_render_servers,
    probe_render_node,
)
if TYPE_CHECKING:
    from .config import Settings
    from .credits_db import CreditsDB
    from .state_store import RedisChatStateStore as StateStore
    from .tbank_client import TBankClient

log = logging.getLogger("admin_panel")

# ── Readable stage names ─────────────────────────────────────────────────

_STAGE_LABELS = {
    "IDLE": "Завершено",
    "WAIT_START": "Ожидает старта",
    "WAIT_SUBSCRIPTION": "Подписка на канал",
    "WAIT_AUDIO": "Загрузка аудио",
    "WAIT_LYRICS_CHOICE": "Выбор текста",
    "WAIT_LYRICS_TEXT": "Ввод текста",
    "WAIT_FRAGMENT_CHOICE": "Выбор фрагмента",
    "WAIT_FRAGMENT_TEXT": "Ввод фрагмента",
    "WAIT_CONFIRM_TEXT": "Подтверждение текста",
    "WAIT_SUBTITLES_MODE": "Выбор субтитров",
    "WAIT_CONFIRM_MODE": "Подтверждение режима",
    "WAIT_VERSIONS": "Выбор версий",
    "WAIT_CONFIRM": "Подтверждение генерации",
    "PROCESSING": "Генерация",
    "WAIT_NEXT": "Ожидает следующего",
    "RATE_VIDEO": "Оценка видео #1",
    "FEEDBACK_LOW": "Фидбек (низкая оценка)",
    "SALES_PITCH": "Питч",
    "PACKAGES_OFFER": "Предложение пакетов",
    "PACKAGE_DETAILS": "Детали пакета",
    "ALL_PACKAGES": "Все пакеты",
    "PACKAGE_INFO": "Инфо о пакете",
    "WHY_NOT": "Почему неактуально",
    "NOT_ACTUAL_REASON": "Причина отказа",
    "CASES_TECH": "Кейсы и технология",
    "TRY_FULL": "Попробовать полностью",
    "REFERRAL_ASK": "Реферал",
    "WAIT_REFERRAL_TAG": "Ввод тега друга",
    "WAITING_REFERRAL": "Ожидание друга",
    "RATE_VIDEO_2": "Оценка видео #2",
    "FEEDBACK_LOW_2": "Фидбек #2",
    "LAST_STEP_FORM": "Последний шаг (форма)",
    "POST_SURVEY": "После опроса",
    "KEEP_IN_TOUCH": "На связи",
    "REMIND_RELEASE": "Напоминание о релизе",
    "NO_FRIENDS_FORM": "Форма (нет друзей)",
}

_EVENT_LABELS = {
    "app_entry": "Вход в веб-приложение",
    "page_view": "Просмотр страницы сайта",
    "wizard_stage_view": "Просмотр этапа визарда",
    "wizard_stage_time": "Время на этапе визарда",
    "wizard_back": "Возврат в визарде",
    "signup_started": "Начало регистрации на сайте",
    "signup_completed": "Регистрация на сайте завершена",
    "project_created": "Проект создан на сайте",
    "track_uploaded": "Трек загружен на сайте",
    "generation_completed": "Ролики готовы на сайте",
    "video_previewed": "Просмотр превью",
    "video_downloaded": "Скачивание ролика",
    "video_download_all": "Скачивание батча",
    "guide_opened": "Методичка открыта",
    "guide_downloaded": "Методичка скачана",
    "pricing_viewed": "Тарифы открыты на сайте",
    "plan_purchased": "Тариф оплачен на сайте",
    "limit_hit": "Достигнут лимит на сайте",
    "video_posted": "Ролик опубликован с сайта",
    "start": "Старт бота",
    "utm_touch": "UTM касание",
    "subscription_ok": "Подписка подтверждена",
    "audio_uploaded": "Аудио загружено",
    "generation_started": "Генерация запущена",
    "generation_done": "Генерация завершена",
    "generation_failed": "Генерация с ошибкой",
    "rate_video": "Оценка видео",
    "sales_pitch": "Просмотр питча",
    "view_packages": "Просмотр пакетов",
    "select_package": "Выбор пакета",
    "purchase_intent": "Заявка на покупку",
    "purchase_intent_recurrent": "Заявка на подписку",
    "referral_sent": "Реферал отправлен",
    "referral_matched": "Реферал сработал",
    "survey_opened": "Открыл форму",
    "survey_done": "Прошёл форму",
    "keep_in_touch": "На связи",
    "reminder_sent": "Напоминание отправлено",
    "no_credits": "Нет кредитов",
    "payment_confirmed": "Оплата подтверждена",
    "subscription_charged": "Подписка списана",
    "subscription_charge_failed": "Подписка: ошибка списания",
    "cancel_subscription_request": "Запрос отмены подписки",
    "admin_activate": "Активация админом",
    "admin_force_reset": "Force reset админом",
    "admin_dm": "Сообщение от менеджера",
    "initial_grant": "Стартовые кредиты",
}

_RATING_LABELS = {
    "low": "До 5",
    "mid_low": "5-6",
    "high": "7-10",
}

_RATING_COLORS = {
    "low": "#ef6a6a",
    "mid_low": "#f0b04a",
    "high": "#4ecf8a",
}

# 4-bucket rating breakdown for the dashboard (low / 5-6 / 7-8 / 9-10).
_RATING_LABELS_V2 = {
    "low": "1-4",
    "mid_low": "5-6",
    "mid_high": "7-8",
    "high": "9-10",
}
_RATING_COLORS_V2 = {
    "low": "#ef6a6a",
    "mid_low": "#f0b04a",
    "mid_high": "#5aa9ea",
    "high": "#4ecf8a",
}

# Funnel grouping for the dashboard mini-cards (split by lifecycle stage).
_FUNNEL_GROUPS = (
    ("Генерация", "#5aa9ea", [
        "start", "subscription_ok", "audio_uploaded",
        "generation_started", "generation_done",
    ]),
    ("Питч", "#9b84ee", [
        "rate_video", "sales_pitch",
    ]),
    ("Продажа", "#4ecf8a", [
        "view_packages", "purchase_intent", "purchase_intent_recurrent",
        "payment_confirmed", "subscription_charged",
    ]),
)
_FUNNEL_OTHER_EVENTS = (
    "survey_opened", "survey_done", "referral_sent", "referral_matched",
    "admin_dm",
)

# Canonical funnel order for visualization.
# Acquisition → activation → engagement → monetization. Branches like feedback
# form / referral / manager DM live alongside the purchase path.
_FUNNEL_ORDER = [
    "start",
    "subscription_ok",
    "audio_uploaded",
    "generation_started",
    "generation_done",
    "rate_video",
    "survey_opened",
    "survey_done",
    "referral_sent",
    "sales_pitch",
    "view_packages",
    "select_package",
    "purchase_intent",
    "payment_confirmed",
    "subscription_charged",
    "admin_dm",
]

# Funnel bar colors (green → red gradient).
_FUNNEL_COLORS = [
    "#4ecf8a", "#4ecf8a", "#5aa9ea", "#5aa9ea",  # acquisition
    "#9b84ee", "#9b84ee", "#4ecf8a", "#4ecf8a", "#f0b04a",  # engagement / loyalty
    "#f0b04a", "#f0b04a", "#ef6a6a",  # interest in packages
    "#ef6a6a", "#ef6a6a", "#8d86ad", "#8d86ad",  # monetization + manager
]

# Package definitions
_PACKAGES = {
    "5": "Триал (5 генераций)",
    "15": "Бласт (100 генераций)",
    "30": "Глоу (400 генераций)",
    "50": "Импульс (безлимит)",
}

# Unique-track allowance granted on manual package activation. Mirrors
# app.BlastBotApp._PKG_TRACKS (keyed here by numeric package code). Trial has
# no track quota → falls back to the user's default. Admin activation is a
# manual "give them this package" action, so it grants the tariff base.
_PACKAGE_TRACKS = {
    "15": 4,
    "30": 10,
    "50": 24,
}

# ── Tier system spec (Reactivation plan v2.0) ────────────────────────────
# 15 tiers: 12 primary (resolved by user_tiers SQL view) + 3 audience-only
# (P2/D1/D2, resolved on-demand for manual broadcasts).
# Each entry has:
#   title, group, color, rule (segmentation), task, channel, angle, cr,
#   delay (when triggered), kind (manager/auto/broadcast),
#   audience_only (bool — true ⇒ not in user_tiers view, computed via dedicated SQL),
#   trigger_text (default message body for the seeded lifecycle rule, optional),
#   trigger_template (lifecycle rule template payload, optional — used by the
#     "Создать триггер" button to pre-fill the form for this tier).

_TIER_SPEC: Dict[str, Dict[str, Any]] = {
    "S1": {
        "title": "Чемпионы",
        "group": "S", "color": "#ef6a6a",
        "rule": "gens ≥ 2 AND last_rating='high' AND feedback_form_clicked AND нет оплаты",
        "task": "Познакомиться, получить ролик и фидбек. Продажа — следующим шагом",
        "channel": "Менеджер DM (голос/текст), 1-1",
        "angle": "Личный контакт менеджера через 24ч после клика по форме",
        "cr": "50-70% в ответ → 25-35% в покупку",
        "delay": "24ч после feedback_form_clicked",
        "kind": "manager",
        "trigger_text": (
            "{{first_name}}, привет! Давай познакомимся — меня зовут {{manager_name}}, "
            "я менеджер Бласта, бота, которым ты пользовался для генерации видео под свой трек.\n\n"
            "Мы только закатили все обновления и начали анализировать обратную связь, поэтому пишу лично.\n\n"
            "Вижу, прошлый ролик оценил высоко — это супер, рад, что вкатило. Отправишь его? "
            "Хочется посмотреть свежим взглядом.\n\n"
            "И ещё: будет круто, если расскажешь — что больше всего понравилось, а чего не хватило."
        ),
    },
    "S2": {
        "title": "Изучили пакет",
        "group": "S", "color": "#ef6a6a",
        "rule": "last_rating='high' AND select_package есть AND feedback_form_clicked НЕТ AND нет оплаты",
        "task": "Довести до клика по форме (далее переходит в S1)",
        "channel": "Автомат + менеджерская эскалация",
        "angle": "Через карточку пакета — к форме обратной связи",
        "cr": "35-50% в feedback_form_clicked",
        "delay": "6-12ч после select_package · эскалация менеджеру через 3 дня без формы",
        "kind": "auto",
        "trigger_text": (
            "{{first_name}}, привет! Спасибо за высокие оценки — рады получать такой фидбэк.\n\n"
            "Видим, заглядывал в карточку пакета — там короткое описание, может не все вопросы закрыть. "
            "Если актуально пообщаться насчёт пакетов и получить третий обещанный ролик, заполни форму "
            "обратной связи, там предметные вопросы, нам поможет: {{form_link}}\n\n"
            "2 минуты на форму, дальше менеджер отпишет в течение дня, добавит баланс на аккаунт и "
            "ответит на вопросы по пакетам, если они есть."
        ),
        "trigger_template": {
            "trigger_type": "time_after_event",
            "trigger": {
                "event": "select_package",
                "hours_min": 6, "hours_max": 720,
                "blocking_events": ["survey_opened"],
                "require_last_rating": "high",
            },
            "cooldown_days": 14,
        },
    },
    "S3": {
        "title": "Пригласил друга, не дошёл до формы",
        "group": "S", "color": "#ef6a6a",
        "rule": "gens=2 AND last_rating='high' AND feedback_form_clicked НЕТ AND select_package НЕТ AND нет оплаты",
        "task": "Шаг 1 — толкнуть к /packets (миграция в S2). Шаг 2 — толкнуть к форме (миграция в S1)",
        "channel": "Автомат, 2 шага",
        "angle": "Сначала /packets, через 72ч — форма",
        "cr": "Шаг 1: 15-25% в select_package · Шаг 2: 20-30% в feedback_form_clicked",
        "delay": "Шаг 1 — 24ч после high-оценки 2-го ролика · Шаг 2 — +72ч без миграции",
        "kind": "auto",
        "trigger_text": (
            "{{first_name}}, привет!\n\n"
            "Видим, ты сделал два ролика, везде высокие оценки — спасибо за обратку, круто, что вкатило.\n\n"
            "Вопрос, который всегда возникает: «а что после двух роликов?». У нас есть ответ: "
            "полноценные пакеты — это про то, как из одной mp3-шки сделать системный контент и "
            "настроить продвижение: блогеры, дистрибьюция, стабильный охват.\n\n"
            "Глянь, что внутри через команду /packets — 100% найдёшь актуальное решение под себя."
        ),
        "trigger_template": {
            "trigger_type": "time_after_event",
            "trigger": {
                "event": "rate_video", "event_detail": "high",
                "hours_min": 24, "hours_max": 720,
                "blocking_events": ["select_package", "survey_opened"],
                "require_gens_eq": 2,
            },
            "cooldown_days": 14,
        },
    },
    "P1": {
        "title": "Trial-юзеры, баланс на исходе",
        "group": "P", "color": "#4ecf8a",
        "rule": "купили Trial AND нет старшего пакета AND баланс ≤ 1 ролик",
        "task": "Апсейл в Бласт-подписку через спецоффер",
        "channel": "Автомат",
        "angle": "Удваиваем ролики при апгрейде в течение 15 дней",
        "cr": "10-20% в апгрейд",
        "delay": "При остатке на балансе ≤ 1 ролик",
        "kind": "auto",
        "trigger_text": (
            "{{first_name}}, привет!\n\n"
            "Триал у тебя на исходе — пора решать, что дальше. Если за эти дни стало ясно, что "
            "инструмент рабочий, имеет смысл сразу переходить на Бласт: больше роликов, блогеры, "
            "дистрибьюция и стабильный охват.\n\n"
            "Под триал-юзеров держим спецусловие: удваиваем ролики в первый месяц, если приобрести "
            "подписку на Бласт в течение 15 дней с момента отправки этого сообщения.\n\n"
            "Если хочется глянуть все пакеты сразу: /packets. Если нужна связь с менеджером — @impulsemanage."
        ),
        "trigger_template": {
            "trigger_type": "low_balance_trial",
            "trigger": {"max_credits": 1},
            "cooldown_days": 14,
            "exclude_paid": False,  # P1 ARE paid (trial); only exclude higher packages internally
        },
    },
    "P2": {
        "title": "Реферреры",
        "group": "P", "color": "#4ecf8a",
        "rule": "referral_sent есть",
        "task": "Удержание лояльности, без жёсткого селла",
        "channel": "Ручная массовая, разовая по событию",
        "angle": "Ранний доступ к фиче — благодарность за рефералы",
        "cr": "Не считаем как продажу. Open rate + удержание",
        "delay": "Ручной запуск по событию (новая фича / закрытая акция)",
        "kind": "broadcast",
        "audience_only": True,
        "trigger_text": (
            "{{first_name}}, привет!\n\n"
            "Помним, что ты приводил друзей в Бласт — это сильно помогает, спасибо.\n\n"
            "Для реферреров открываем ранний доступ к {{exclusive_thing}} — никому больше пока не "
            "показываем: {{details_or_link}}.\n\n"
            "Доступ до {{deadline}}, платить ничего не нужно — это благодарность за то, что делишься."
        ),
    },
    "P3": {
        "title": "Тыкуны с генерациями + high",
        "group": "P", "color": "#4ecf8a",
        "rule": "purchase_intent AND нет оплаты за 24ч AND gens ≥ 1 AND last_rating='high'",
        "task": "Закрыть оплату через личный контакт",
        "channel": "Менеджер 1-1",
        "angle": "Уведомление менеджеру с контекстом + ручное касание",
        "cr": "25-35% в реальную оплату",
        "delay": "5 минут после purchase_intent (через уведомление менеджеру)",
        "kind": "manager",
        "trigger_text": (
            "{{first_name}}, привет! {{manager_name}} на связи.\n\n"
            "Вижу, ты нажимал «оплатить», но оплата не прошла, что-то остановило в моменте. "
            "Хочу разобраться лично: технический сбой, цена или появились вопросы по пакету?\n\n"
            "Если технический — сделаем свежую ссылку. Если по пакету — расскажу подробнее, подберу "
            "под задачу. Если решил повременить — тоже скажи, чтобы не дёргать лишний раз."
        ),
    },
    "P4": {
        "title": "Тыкуны базовые",
        "group": "P", "color": "#4ecf8a",
        "rule": "purchase_intent AND нет оплаты за 24ч AND NOT IN P3",
        "task": "Вернуть в существующий флоу через /sendtrack или /packets",
        "channel": "Автомат",
        "angle": "Мягкое напоминание + три CTA",
        "cr": "3-8% в оплату; остальное — возврат в флоу без потерь",
        "delay": "5 минут после purchase_intent",
        "kind": "auto",
        "trigger_text": (
            "{{first_name}}, привет!\n\n"
            "Видим, ты нажимал «оплатить», но не дошёл до конца — возможно, передумал, возможно, "
            "технический сбой.\n\n"
            "Если хочется ещё раз посмотреть пакеты и подобрать другой — /packets. Если хочется "
            "сначала собрать ролик и решить после — /sendtrack. Если зависла оплата — пиши "
            "@impulsemanage, разберёмся быстро."
        ),
        "trigger_template": {
            "trigger_type": "tier_membership",
            "trigger": {"tier": "P4"},
            "cooldown_days": 3,
            "exclude_paid": False,
        },
    },
    "A1": {
        "title": "Залипли на 1-м ролике",
        "group": "A", "color": "#f0b04a",
        "rule": "gens=1 AND last_rating='high' AND referral_sent НЕТ AND survey_opened НЕТ AND нет оплаты",
        "task": "Дотянуть до отправки реферала → миграция к 2-му ролику и далее в S3",
        "channel": "Автомат",
        "angle": "Подсказка про второй ролик через кнопки",
        "cr": "25-35% в referral_sent",
        "delay": "1ч после rate_video='high'",
        "kind": "auto",
        "trigger_text": (
            "{{first_name}}, привет!\n\n"
            "Видим, оценил первый ролик высоко — кайф, что зашло.\n\n"
            "Чтобы открыть второй, нужно просто нажать на любую кнопку рядом с клавиатурой и нырнуть "
            "глубже в бота, там будет вся информация."
        ),
        "trigger_template": {
            "trigger_type": "time_after_event",
            "trigger": {
                "event": "rate_video", "event_detail": "high",
                "hours_min": 1, "hours_max": 720,
                "blocking_events": ["survey_opened"],
                "require_gens_eq": 1, "require_no_referral": True,
            },
            "cooldown_days": 30,
        },
    },
    "A2": {
        "title": "Не запустили генерацию",
        "group": "A", "color": "#f0b04a",
        "rule": "audio_uploaded есть AND gens_done=0 AND нет оплаты",
        "task": "Довести до generation_started",
        "channel": "Автомат, 2 шага",
        "angle": "Шаг 1 — короткий пинок. Шаг 2 — предложение разобраться через @impulsemanage",
        "cr": "30-50% в generation_started",
        "delay": "Шаг 1 — 30 мин после audio_uploaded · Шаг 2 — 24ч после audio_uploaded",
        "kind": "auto",
        "trigger_text": (
            "{{first_name}}, всё готово — аудио залил, осталось подтвердить настройки и ткнуть "
            "«сгенерировать». Ролик будет моментально запущен в работу. Продолжай!"
        ),
        "trigger_template": {
            "trigger_type": "time_after_event",
            "trigger": {
                "event": "audio_uploaded",
                "hours_min": 0.5, "hours_max": 24,
                "blocking_events": ["generation_started", "generation_done"],
                "require_gens_eq": 0,
            },
            "cooldown_days": 14,
        },
    },
    "A3": {
        "title": "Не оценили ролик",
        "group": "A", "color": "#f0b04a",
        "rule": "gens=1 AND rate_video НЕТ AND нет оплаты",
        "task": "Получить оценку → юзер автоматически распределится дальше",
        "channel": "Автомат с персонализацией, 2 шага",
        "angle": "Оценка → второй ролик и продолжение бота",
        "cr": "25-35% в rate_video",
        "delay": "Шаг 1 — 1ч после generation_done · Шаг 2 — 24ч после generation_done",
        "kind": "auto",
        "trigger_text": (
            "{{first_name}}, ролик готов — что думаешь?\n\n"
            "Поставь оценку ролику через кнопки ниже, это ровно секунда. Зачем: оценка откроет "
            "второй ролик и продолжение бота.\n\n"
            "Давай продолжим делать контент!"
        ),
        "trigger_template": {
            "trigger_type": "time_after_event",
            "trigger": {
                "event": "generation_done",
                "hours_min": 1, "hours_max": 720,
                "blocking_events": ["rate_video"],
                "require_gens_eq": 1,
            },
            "cooldown_days": 7,
        },
    },
    "B1": {
        "title": "Средняя оценка, не пошли дальше",
        "group": "B", "color": "#f0b04a",
        "rule": "gens=1 AND last_rating='mid_low' AND survey_opened НЕТ AND нет оплаты",
        "task": "Довести до 2-й генерации",
        "channel": "Автомат",
        "angle": "Норм-фрейминг + предложение второй попытки или менеджера",
        "cr": "15-25% во вторую генерацию",
        "delay": "1ч после rate_video='mid_low'",
        "kind": "auto",
        "trigger_text": (
            "{{first_name}}, привет! Видим, первый ролик зашёл средненько — бывает, не каждый ролик "
            "ловит вайб трека с первой попытки.\n\n"
            "Это норм. Попробуй сгенерировать ещё раз, но с другими настройками: часто вторая "
            "генерация попадает точнее, чем первая. Если хочется сначала обсудить, что не зашло, — "
            "пиши @impulsemanage, разберёмся"
        ),
        "trigger_template": {
            "trigger_type": "time_after_event",
            "trigger": {
                "event": "rate_video", "event_detail": "mid_low",
                "hours_min": 1, "hours_max": 720,
                "blocking_events": ["survey_opened"],
                "require_gens_eq": 1,
            },
            "cooldown_days": 30,
        },
    },
    "B2": {
        "title": "Низкая оценка, не дошли до формы",
        "group": "B", "color": "#f0b04a",
        "rule": "last_rating='low' AND feedback_form_clicked НЕТ AND нет оплаты",
        "task": "Довести до клика по форме (далее ручной разбор менеджером)",
        "channel": "Автомат",
        "angle": "Эмпатия + форма + обещание баланса на пересборку",
        "cr": "15-25% в feedback_form_clicked. Главное — данные о причинах",
        "delay": "1ч после rate_video='low'",
        "kind": "auto",
        "trigger_text": (
            "{{first_name}}, привет! Видим, первый ролик зашёл слабо — может быть технический сбой, "
            "может настройки не подобрались, может что-то ещё.\n\n"
            "Хочется разобраться, а не оставлять как есть. Заполни короткую форму обратной связи — "
            "это 30 секунд: {{form_link}}. После формы менеджер отпишет лично, разберет ситуацию и "
            "закинет баланс на пересборку — на этот раз уже с понятными нам вводными."
        ),
        "trigger_template": {
            "trigger_type": "time_after_event",
            "trigger": {
                "event": "rate_video", "event_detail": "low",
                "hours_min": 1, "hours_max": 720,
                "blocking_events": ["survey_opened"],
            },
            "cooldown_days": 30,
        },
    },
    "B3": {
        "title": "Стартовали, не подписались",
        "group": "B", "color": "#f0b04a",
        "rule": "start есть AND subscription_ok НЕТ AND gens_done=0 AND bot_blocked=false",
        "task": "Довести до subscription_ok",
        "channel": "Автомат",
        "angle": "Что внутри + насколько быстро",
        "cr": "5-10% в подписку",
        "delay": "1ч после start",
        "kind": "auto",
        "trigger_text": (
            "{{first_name}}, привет!\n\n"
            "Видим, ты зашёл в бота, но до подписки на канал не добрался — а это первый шаг, чтобы "
            "начать собирать ролики.\n\n"
            "Что внутри: бот за 30 секунд делает видео под твой трек, нужны только mp3 и базовые "
            "настройки. Подписка — секунда, дальше сразу едем генерировать контент."
        ),
        "trigger_template": {
            "trigger_type": "time_after_event",
            "trigger": {
                "event": "start",
                "hours_min": 1, "hours_max": 720,
                "blocking_events": ["subscription_ok"],
                "require_gens_eq": 0,
            },
            "cooldown_days": 14,
        },
    },
    "D1": {
        "title": "Старая база по источникам",
        "group": "D", "color": "#9b84ee",
        "rule": "last_active < NOW() − 30д AND нет оплаты",
        "task": "Заинтересовать апдейтом и вернуть в флоу через /sendtrack",
        "channel": "Ручная массовая, по источнику",
        "angle": "Что нового + кейс + бонус-баланс на пересборку",
        "cr": "3-7% в /sendtrack-активацию",
        "delay": "Ручной запуск менеджером по апдейту/кейсу/акции",
        "kind": "broadcast",
        "audience_only": True,
        "trigger_text": (
            "{{first_name}}, привет! Это Бласт.\n\n"
            "Ты заходил к нам {{time_ago}} назад, но до результата не добрался. С тех пор кое-что "
            "изменилось — рассказываю коротко.\n\n"
            "Что нового: {{whats_new}} — стало {{key_improvement}}.\n\n"
            "Свежий кейс на выходе: {{case_link}}\n\n"
            "До {{deadline}} закидываем {{bonus_amount}} на баланс всем, кто заходил из этой волны — "
            "на пересборку трека с актуальными настройками. Заходи и пробуй: /sendtrack"
        ),
    },
    "D2": {
        "title": "Старая база, смотрели пакет, оценили high",
        "group": "D", "color": "#9b84ee",
        "rule": "select_package есть AND last_rating='high' AND last_active < NOW() − 30д AND нет оплаты",
        "task": "Вернуть к разговору о пакетах с упоминанием обновлений",
        "channel": "Ручная массовая, малый горячий сегмент",
        "angle": "Обновления пакетов + менеджер на связи",
        "cr": "10-15% в покупку",
        "delay": "Разовая ручная массовая по сегменту",
        "kind": "broadcast",
        "audience_only": True,
        "trigger_text": (
            "{{first_name}}, привет! На связи команда Бласта.\n\n"
            "Чуть больше месяца назад ты заглядывал в один из наших пакетов и поставил роликам "
            "высокие оценки — пишем, вдруг сейчас актуально.\n\n"
            "С тех пор пакеты обновились: {{packages_updates}}. Цены остались на месте.\n\n"
            "Если интересно — глянь актуальные пакеты через /packets. Если нужна связь с "
            "менеджером — @impulsemanage, разложит за 5 минут, что изменилось и подойдёт ли тебе сейчас."
        ),
    },
}

_TIER_GROUPS = [
    ("S", "Горячие", "Менеджер вручную, голосовые, 1-1", "#ef6a6a"),
    ("P", "Спец-сегменты", "Платящие, реферреры, тыкуны", "#4ecf8a"),
    ("A", "Тёплые", "Автомат + персонализация", "#f0b04a"),
    ("B", "Средние", "Автомат-триггеры по оценкам", "#f0b04a"),
    ("D", "Старая база", "Ручная массовая реактивация", "#9b84ee"),
]

# Default lifecycle rules seeded on first deploy. Each rule is created in
# enabled=False so the admin can preview audience and test-send before flipping.
_DEFAULT_LIFECYCLE_RULES: List[Dict[str, Any]] = [
    {
        "tier": "S2",
        "name": "S2 · Изучили пакет (6-12ч)",
        "trigger_type": "time_after_event",
        "trigger": {
            "event": "select_package",
            "hours_min": 6, "hours_max": 720,
            "blocking_events": ["survey_opened"],
            # last rate_video must be 'high' — matches tier definition exactly.
            "require_last_rating": "high",
        },
        "message_text": _TIER_SPEC["S2"]["trigger_text"],
        "cooldown_days": 14,
        "exclude_paid": True,
        "respect_anti_fatigue": True,
    },
    {
        "tier": "S3",
        "name": "S3 · Шаг 1 — толкаем к /packets (24ч)",
        "trigger_type": "time_after_event",
        "trigger": {
            "event": "rate_video", "event_detail": "high",
            "hours_min": 24, "hours_max": 72,
            # If they clicked package or opened survey, they migrated out of S3
            # (to S2 or S1 respectively) — don't re-message.
            "blocking_events": ["select_package", "survey_opened"],
            "require_gens_eq": 2,
        },
        "message_text": _TIER_SPEC["S3"]["trigger_text"],
        "cooldown_days": 14,
        "exclude_paid": True,
        "respect_anti_fatigue": True,
    },
    {
        "tier": "S3",
        "name": "S3 · Шаг 2 — толкаем к форме (72ч)",
        "trigger_type": "time_after_event",
        "trigger": {
            "event": "rate_video", "event_detail": "high",
            "hours_min": 72, "hours_max": 720,
            "blocking_events": ["select_package", "survey_opened"],
            "require_gens_eq": 2,
        },
        "message_text": (
            "{{first_name}}, снова привет!\n\n"
            "Ещё подсвечу: третий бесплатный ролик всё ещё доступен — нужно всего "
            "лишь пройти короткую форму обратной связи: {{form_link}}\n\n"
            "Это финальный бесплатный ролик. После формы менеджер отпишет лично, "
            "подкинет кредит на баланс и ответит на все вопросы, если они есть."
        ),
        "cooldown_days": 30,
        "exclude_paid": True,
        # Step 2 of an intentional cascade — anti-fatigue would otherwise flag step
        # 1's send within the last 7 days. Own cooldown_days=30 prevents re-fire.
        "respect_anti_fatigue": False,
    },
    {
        "tier": "B3",
        "name": "B3 · Стартовали, не подписались (1ч)",
        "trigger_type": "time_after_event",
        "trigger": {
            "event": "start",
            "hours_min": 1, "hours_max": 720,
            "blocking_events": ["subscription_ok"],
            # Strict gens=0: users who restarted /start after generating a video
            # have an old `start` event but generation_done events too — they
            # shouldn't be classified as "стартовали, не подписались".
            "require_gens_eq": 0,
        },
        "message_text": _TIER_SPEC["B3"]["trigger_text"],
        "cooldown_days": 14,
        "exclude_paid": True,
        "respect_anti_fatigue": True,
    },
    {
        "tier": "A2",
        "name": "A2 · Шаг 1 — не запустили генерацию (30 мин)",
        "trigger_type": "time_after_event",
        "trigger": {
            "event": "audio_uploaded",
            "hours_min": 0.5, "hours_max": 24,
            # Block on generation_done as well as generation_started — old data
            # sometimes has missing started events but completed gens.
            "blocking_events": ["generation_started", "generation_done"],
            "require_gens_eq": 0,
        },
        "message_text": _TIER_SPEC["A2"]["trigger_text"],
        "cooldown_days": 14,
        "exclude_paid": True,
        "respect_anti_fatigue": True,
    },
    {
        "tier": "A2",
        "name": "A2 · Шаг 2 — не запустили генерацию (24ч)",
        "trigger_type": "time_after_event",
        "trigger": {
            "event": "audio_uploaded",
            "hours_min": 24, "hours_max": 720,
            "blocking_events": ["generation_started", "generation_done"],
            "require_gens_eq": 0,
        },
        "message_text": (
            "{{first_name}}, твой трек всё ещё ждёт генерации.\n\n"
            "Если что-то непонятно с настройками, глючит интерфейс или есть вопрос — "
            "пиши сюда, разберёмся: @impulsemanage\n\n"
            "Без запуска в боте ролик не появится сам по себе."
        ),
        "cooldown_days": 30,
        "exclude_paid": True,
        # Step 2 of an intentional cascade — anti-fatigue would block it because
        # step 1 already sent in the last 48h. Own cooldown_days=30 prevents
        # re-fire of step 2 itself.
        "respect_anti_fatigue": False,
    },
    {
        "tier": "A3",
        "name": "A3 · Не оценили ролик — шаг 1 (1ч)",
        "trigger_type": "time_after_event",
        "trigger": {
            "event": "generation_done",
            "hours_min": 1, "hours_max": 24,
            "blocking_events": ["rate_video"],
            "require_gens_eq": 1,
        },
        "message_text": _TIER_SPEC["A3"]["trigger_text"],
        "cooldown_days": 7,
        "exclude_paid": True,
        "respect_anti_fatigue": True,
    },
    {
        "tier": "A3",
        "name": "A3 · Не оценили ролик — шаг 2 (24ч)",
        "trigger_type": "time_after_event",
        "trigger": {
            "event": "generation_done",
            "hours_min": 24, "hours_max": 720,
            "blocking_events": ["rate_video"],
            "require_gens_eq": 1,
        },
        "message_text": _TIER_SPEC["A3"]["trigger_text"],
        "cooldown_days": 30,
        "exclude_paid": True,
        # Step 2 of an intentional cascade — anti-fatigue would otherwise block it
        # because step 1 already sent a message in the last 48h. Own cooldown_days=30
        # prevents re-fire of step 2 itself.
        "respect_anti_fatigue": False,
    },
    {
        "tier": "A1",
        "name": "A1 · Залипли на 1-м ролике (1ч)",
        "trigger_type": "time_after_event",
        "trigger": {
            "event": "rate_video", "event_detail": "high",
            "hours_min": 1, "hours_max": 720,
            "blocking_events": ["survey_opened"],
            "require_gens_eq": 1, "require_no_referral": True,
        },
        "message_text": _TIER_SPEC["A1"]["trigger_text"],
        "cooldown_days": 30,
        "exclude_paid": True,
        "respect_anti_fatigue": True,
    },
    {
        "tier": "B1",
        "name": "B1 · Средняя оценка (1ч)",
        "trigger_type": "time_after_event",
        "trigger": {
            "event": "rate_video", "event_detail": "mid_low",
            "hours_min": 1, "hours_max": 720,
            "blocking_events": ["survey_opened"],
            "require_gens_eq": 1,
        },
        "message_text": _TIER_SPEC["B1"]["trigger_text"],
        "cooldown_days": 30,
        "exclude_paid": True,
        "respect_anti_fatigue": True,
    },
    {
        "tier": "B2",
        "name": "B2 · Низкая оценка (1ч)",
        "trigger_type": "time_after_event",
        "trigger": {
            "event": "rate_video", "event_detail": "low",
            "hours_min": 1, "hours_max": 720,
            "blocking_events": ["survey_opened"],
        },
        "message_text": _TIER_SPEC["B2"]["trigger_text"],
        "cooldown_days": 30,
        "exclude_paid": True,
        "respect_anti_fatigue": True,
    },
]

# Legacy seed rule names that have been renamed in the spec. The seed function
# drops these from the DB on startup IF they're still pristine (system_seed,
# disabled, never fired). Keeps the admin UI free of stale ghost entries.
_LEGACY_LIFECYCLE_NAMES_TO_DROP: List[Dict[str, str]] = [
    {"tier": "A1", "name": "A1 · Залипли на 1-м ролике (24ч)"},
    {"tier": "A3", "name": "A3 · Не оценили ролик (24-48ч)"},
]


def _fmt_ts(value: Any) -> str:
    """Format datetime / string timestamps consistently for the admin UI."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)

# ── HTML templates (inline to keep it self-contained) ────────────────────

_BASE_HEAD = """
<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blast Admin</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22%20viewBox=%220%200%2064%2064%22%20width=%2264%22%20height=%2264%22%3E%3Crect%20width=%2264%22%20height=%2264%22%20rx=%2214%22%20fill=%22%23140E24%22/%3E%3Cg%20transform=%22translate(10%2010)%20scale(0.7333)%22%3E%3Cpath%20d=%22M40.0775%200.143134C41.8887%20-1.66229%2038.9564%2014.0991%2042.0393%2019.9667C43.4025%2022.5615%2060.1717%2029.2646%2059.9925%2030.0771C59.771%2031.0819%2021.8522%2041.1943%2022.1242%2041.6683C22.4869%2042.3002%2028.6817%2044.3039%2028.8939%2045.6943C29.106%2047.0846%2019.404%2060.0837%2019.132%2059.9935C18.86%2059.9032%2020.7311%2043.4375%2017.6504%2039.646C14.5697%2035.8546%200.9496%2030.6496%200.0598437%2029.8967C-1.75358%2028.362%2038.2546%2018.8044%2037.5056%2018.2516C34.6925%2016.1753%2030.7019%2015.6699%2030.8288%2013.7742C30.9195%2012.4201%2039.6462%200.572985%2040.0775%200.143134Z%22%20fill=%22url(%23blastStar)%22/%3E%3C/g%3E%3Cdefs%3E%3ClinearGradient%20id=%22blastStar%22%20x1=%2241.1656%22%20y1=%22-18.1821%22%20x2=%2211.1191%22%20y2=%2267.9303%22%20gradientUnits=%22userSpaceOnUse%22%3E%3Cstop%20stop-color=%22%23F6F5FD%22/%3E%3Cstop%20offset=%220.495192%22%20stop-color=%22%238B6FE6%22/%3E%3Cstop%20offset=%221%22%20stop-color=%22%235F42B9%22/%3E%3C/linearGradient%3E%3C/defs%3E%3C/svg%3E">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>if (window.Chart) { Chart.defaults.color = "#8d86ad"; Chart.defaults.borderColor = "rgba(255,255,255,.06)"; Chart.defaults.font.family = "Point, Segoe UI, system-ui, sans-serif"; }</script>
<style>
  @font-face { font-family:'Point'; src:url('/admin/static/fonts/Point-Regular.ttf') format('truetype'); font-weight:400; font-style:normal; font-display:swap; }
  @font-face { font-family:'Point'; src:url('/admin/static/fonts/Point-Book.ttf') format('truetype'); font-weight:350; font-style:normal; font-display:swap; }
  @font-face { font-family:'Point'; src:url('/admin/static/fonts/PointBold.ttf') format('truetype'); font-weight:700; font-style:normal; font-display:swap; }
  @font-face { font-family:'Point'; src:url('/admin/static/fonts/Point-SemiBold.ttf') format('truetype'); font-weight:600; font-style:normal; font-display:swap; }

  /* Direction «Студия» (taste-skill high-end / soft structuralism, dials 6/4/4):
     silver-grey ground, white floating panels with one diffuse tinted shadow,
     brand violet kept as the single accent — desaturated, no gradients, no glow. */
  :root {
    --bg: #07030f;
    --surface: #110b20;
    --surface-2: #1a1331;
    --surface-3: #241b42;
    --border: #221a3c;
    --border-strong: #322858;
    --text: #f2effc;
    --text-70: #c6bfe4;
    --text-50: #8d86ad;
    --text-35: #6a6390;
    --g-start: #9b84ee;
    --g-end: #9b84ee;
    --accent: #9b84ee;
    --accent-strong: #7c64e0;
    --accent-soft: rgba(155,132,238,.14);
    --ok: #4ecf8a; --ok-bg: rgba(78,207,138,.12);
    --warn: #f0b04a; --warn-bg: rgba(240,176,74,.14);
    --danger: #ef6a6a; --danger-bg: rgba(239,106,106,.14);
    --info: #5aa9ea; --info-bg: rgba(90,169,234,.14);
    --r-card: 18px;
    --r-btn: 999px;
    --r-input: 10px;
    --r-badge: 999px;
    --shadow: 0 1px 2px rgba(0,0,0,.35), 0 18px 44px -22px rgba(0,0,0,.7);
  }

  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  html { color-scheme: dark; }
  body { font-family: 'Point', 'Segoe UI', system-ui, sans-serif; max-width: 1360px; margin: 0 auto; padding: 0 2rem 3rem; background: var(--bg); color: var(--text); font-weight: 400; font-size: 14px; line-height: 1.45; -webkit-font-smoothing: antialiased; font-variant-numeric: tabular-nums; }
  h1 { margin: 1.6rem 0 1.1rem; font-weight: 700; font-size: 2rem; letter-spacing: -0.035em; line-height: 1; }
  h2 { margin: 1.4rem 0 0.6rem; color: var(--text); font-weight: 600; font-size: 1.07em; letter-spacing: -0.01em; }
  h3 { margin: 1.1rem 0 0.5rem; color: var(--text-70); font-weight: 600; font-size: 0.95em; }
  p { color: var(--text-50); }
  code { font-family: ui-monospace, Menlo, monospace; font-size: .92em; }

  @keyframes fadeUp { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
  @media (prefers-reduced-motion: no-preference) {
    .card { animation: fadeUp .4s cubic-bezier(.16,1,.3,1) both; }
    .nav a, button, .btn, tr, .pagination a, input, select { transition: background .16s ease, color .16s ease, transform .12s ease, opacity .16s ease, border-color .16s ease, box-shadow .16s ease; }
  }

  /* Navigation: logo · pill segment · search */
  .header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 18px 0 4px; flex-wrap: wrap; }
  .header .brand { display: flex; align-items: center; gap: 10px; text-decoration: none; color: var(--text); font-weight: 700; font-size: 1.15em; letter-spacing: -0.02em; }
  .header .brand svg { width: 28px; height: 28px; display: block; }
  .header .brand small { font-weight: 400; color: var(--text-50); font-size: .78em; margin-left: 2px; }
  .nav { display: flex; align-items: center; background: var(--surface); border-radius: 999px; padding: 3px; box-shadow: var(--shadow); gap: 2px; flex-wrap: wrap; }
  .nav a { color: var(--text-70); text-decoration: none; padding: 7px 13px 5px; border-radius: 999px; font-size: 0.9em; white-space: nowrap; }
  .nav a:hover { background: var(--surface-2); color: var(--text); text-decoration: none; }
  .nav a.active { background: var(--text); color: var(--bg); font-weight: 600; }
  .nav details { position: relative; }
  .nav details summary { list-style: none; cursor: pointer; color: var(--text-70); padding: 7px 13px 5px; border-radius: 999px; font-size: 0.9em; }
  .nav details summary::-webkit-details-marker { display: none; }
  .nav details summary:hover, .nav details[open] summary { background: var(--surface-2); color: var(--text); }
  .nav details .menu { position: absolute; right: 0; top: calc(100% + 8px); background: var(--surface); border-radius: 14px; box-shadow: var(--shadow); padding: 6px; display: grid; grid-template-columns: repeat(2, minmax(150px, 1fr)); gap: 2px; z-index: 30; }
  .nav details .menu a { border-radius: 10px; }
  .header .search-form { display: flex; gap: 6px; }
  .header .search-form input { padding: 8px 14px 6px; border: 1px solid transparent; border-radius: 999px; font-size: 0.9em; width: 200px; background: var(--surface); color: var(--text); box-shadow: var(--shadow); }
  .header .search-form input::placeholder { color: var(--text-35); }
  .header .search-form button { padding: 8px 14px 6px; }

  /* Panels */
  .card { background: var(--surface); border: 0; border-radius: var(--r-card); padding: 1.35rem 1.5rem; margin: 1.1rem 0; box-shadow: var(--shadow); }
  .card.hero { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 12px; padding: 2.6rem 1.5rem 2.4rem; }
  .card.hero p { margin: 0; max-width: 56ch; }
  .card > h2 + .stage-grid, .card > h2 + .table-wrap { margin-top: .4rem; }
  .filters { display: flex; gap: 14px; align-items: flex-end; flex-wrap: wrap; }
  .filters .f { display: flex; flex-direction: column; gap: 5px; font-size: .82em; color: var(--text-50); }
  .filters .f input, .filters .f select { min-width: 150px; }
  .filters .spacer { flex: 1; }
  .toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
  .card > h2:first-child { margin-top: 0; }

  /* Tables */
  .table-wrap { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; margin: 0.5rem 0; }
  th, td { border-bottom: 1px solid var(--border); padding: 9px 12px; text-align: left; font-size: 0.92em; }
  th { background: transparent; color: var(--text-50); font-weight: 600; font-size: 0.82em; letter-spacing: .01em; border-bottom: 1px solid var(--border-strong); }
  tr:hover td { background: rgba(255,255,255,.025); }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }

  /* Forms */
  form { display: inline; }
  input[type=number], input[type=text], input[type=password], input[type=date], input[type=datetime-local], input[type=month], textarea {
    padding: 7px 12px; border: 1px solid var(--border-strong); border-radius: var(--r-input); font-size: 0.92em; background: var(--surface); color: var(--text); font-family: inherit;
  }
  input::placeholder, textarea::placeholder { color: var(--text-35); }
  select { padding: 7px 12px; border: 1px solid var(--border-strong); border-radius: var(--r-input); font-size: 0.92em; background: var(--surface); color: var(--text); font-family: inherit; }
  input:focus-visible, select:focus-visible, textarea:focus-visible, button:focus-visible, a:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  button, .btn { display: inline-block; padding: 8px 16px 6px; cursor: pointer; border: none; border-radius: var(--r-btn); font-size: 0.9em; font-family: inherit; font-weight: 600; background: var(--accent-strong); color: #fff; text-decoration: none; line-height: 1.3; }
  button:hover, .btn:hover { background: var(--accent); color: #fff; text-decoration: none; }
  .btn-secondary, button.btn-secondary { background: var(--surface-2); color: var(--text-70); }
  .btn-secondary:hover, button.btn-secondary:hover { background: var(--surface-3); color: var(--text); }
  .btn-lg { padding: 16px 34px 14px; font-size: 1.12em; }
  .btn-sm, button.btn-sm { padding: 5px 12px 3px; font-size: .82em; }
  button:active, .btn:active { transform: scale(.98); }
  .btn-danger { background: var(--danger-bg); color: var(--danger); }
  .btn-danger:hover { background: var(--danger); color: #fff; }
  .btn-success { background: var(--ok-bg); color: var(--ok); }
  .btn-success:hover { background: var(--ok); color: #07030f; }

  /* Badges */
  .badge { display: inline-block; padding: 4px 10px 2px; border-radius: var(--r-badge); font-size: 0.82em; font-weight: 600; line-height: 1.3; }
  .badge-ok { background: var(--ok-bg); color: var(--ok); }
  .badge-warn { background: var(--warn-bg); color: var(--warn); }
  .badge-zero { background: var(--danger-bg); color: var(--danger); }
  .badge-stage { background: var(--accent-soft); color: var(--accent); }
  .badge-source { background: var(--info-bg); color: var(--info); }

  /* Funnel (legacy pages) */
  .funnel-bar-wrap { text-align: center; margin: 4px 0; }
  .funnel-bar { display: inline-flex; justify-content: space-between; align-items: center;
                padding: 7px 18px; border-radius: 999px; color: #fff; font-size: 0.88em;
                min-width: 140px; font-weight: 500; }
  .funnel-bar .flabel { text-align: left; }
  .funnel-bar .fcount { font-weight: 700; margin-left: 12px; white-space: nowrap; }

  /* Stage mini-grid */
  .stage-grid { display: flex; flex-wrap: wrap; gap: 8px; margin: 0.6rem 0; }
  .stage-chip { background: var(--surface-2); border: 0; border-radius: 14px; padding: 12px 16px; text-align: left; font-size: 0.85em; min-width: 140px; display: flex; flex-direction: column-reverse; gap: 6px; }
  .stage-chip .count { font-weight: 700; color: var(--text); font-size: 1.75em; letter-spacing: -0.02em; line-height: 1; }
  .stage-chip .label { color: var(--text-50); font-size: 0.95em; }

  /* Chart container */
  .chart-row { display: flex; flex-wrap: wrap; gap: 1.5rem; align-items: flex-start; }
  .chart-box { flex: 0 0 280px; }
  .chart-box canvas { max-width: 280px; max-height: 280px; }
  .funnel-box { flex: 1; min-width: 320px; }

  /* Pagination */
  .pagination { display: flex; gap: 4px; align-items: center; margin: 1rem 0; flex-wrap: wrap; }
  .pagination a, .pagination span { padding: 6px 12px 4px; border-radius: 999px; font-size: 0.9em; }
  .pagination a { background: var(--surface-2); color: var(--text-70); }
  .pagination a:hover { background: var(--border); text-decoration: none; }
  .pagination .current { background: var(--text); color: var(--bg); font-weight: 600; }

  /* Segmented pills (period / channel / bucket toggles) */
  .seg { display: inline-flex; background: var(--surface-2); border-radius: 999px; padding: 3px; gap: 2px; }
  .seg a, .seg span { padding: 6px 14px 4px; border-radius: 999px; color: var(--text-50); font-size: .9em; text-decoration: none; line-height: 1.3; }
  .seg a:hover { color: var(--text); text-decoration: none; }
  .seg .on { background: var(--text); color: var(--bg); font-weight: 600; }

  /* Stat tiles (legacy KPI cards) */
  .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 0.5rem 0 1rem; }
  .stat-tile { background: var(--surface-2); border: 0; border-radius: 14px; padding: 1rem 1.2rem; }
  .stat-tile .stat-label { color: var(--text-50); font-size: 0.82em; margin-bottom: 6px; }
  .stat-tile .stat-value { font-size: 1.7em; font-weight: 700; letter-spacing: -0.02em; }
  .stat-tile.accent .stat-value { color: var(--accent); }

  /* Breadcrumbs: the one way out of a sub-section */
  .crumbs { display: flex; align-items: center; gap: 8px; margin: 1.4rem 0 -0.6rem; font-size: .9em; color: var(--text-35); }
  .crumbs a { color: var(--text-50); text-decoration: none; display: inline-flex; align-items: center; gap: 6px; padding: 5px 11px 3px 8px; border-radius: 999px; background: var(--surface); box-shadow: var(--shadow); }
  .crumbs a::before { content: "←"; color: var(--text-35); }
  .crumbs a + .sep + a::before { content: ""; }
  .crumbs a:hover { color: var(--text); text-decoration: none; }
  .crumbs .sep { color: var(--text-35); }
  .crumbs + h1, .crumbs + .pm-head { margin-top: 1.4rem; }
  .crumbs + .card { margin-top: 1.4rem; }
  .donut { position: relative; flex: 1 1 0; min-width: 120px; max-width: 280px; aspect-ratio: 1; align-self: center; }
  .donut canvas { position: absolute; inset: 0; }
  /* Forms as fields */
  .field { display: flex; flex-direction: column; gap: 6px; }
  .field > span { font-size: .84em; color: var(--text-50); }
  .field input[type=text], .field input[type=number], .field input[type=date], .field input[type=datetime-local], .field textarea, .field select { width: 100%; }
  .field textarea { min-height: 120px; resize: vertical; line-height: 1.5; }
  .stack { display: flex; flex-direction: column; gap: 14px; }
  .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
  /* Radio cards */
  .choice-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; }
  .choice { position: relative; display: flex; flex-direction: column; gap: 4px; padding: 14px 16px 12px; border-radius: 14px; background: var(--surface-2); cursor: pointer; border: 1px solid transparent; }
  .choice input { position: absolute; opacity: 0; inset: 0; margin: 0; cursor: pointer; }
  .choice b { font-weight: 600; }
  .choice small { color: var(--text-50); font-size: .84em; }
  .choice:has(input:checked) { border-color: var(--accent); background: var(--accent-soft); }
  .choice:has(input:focus-visible) { outline: 2px solid var(--accent); outline-offset: 2px; }
  .choice-body { display: none; grid-column: 1 / -1; padding: 6px 2px 2px; }
  .choice-body.open { display: block; }
  /* Telegram-like preview */
  .tg-preview { background: #0e1621; border-radius: 16px; padding: 18px; min-height: 200px; display: flex; flex-direction: column; gap: 10px; }
  .tg-bubble { align-self: flex-start; max-width: 100%; background: #182533; color: #e9edf1; border-radius: 14px 14px 14px 4px; padding: 10px 12px; font-size: .95em; line-height: 1.45; white-space: pre-wrap; word-break: break-word; }
  .tg-bubble .media { background: #223344; color: #8aa0b5; border-radius: 10px; height: 120px; display: flex; align-items: center; justify-content: center; margin-bottom: 8px; font-size: .85em; }
  .tg-buttons { display: flex; flex-direction: column; gap: 4px; max-width: 100%; }
  .tg-buttons span { background: rgba(255,255,255,.08); color: #6fb2ff; text-align: center; padding: 8px 10px 6px; border-radius: 8px; font-size: .9em; }
  .tg-preview .who { color: #8aa0b5; font-size: .8em; }
  .sticky { position: sticky; top: 16px; }
  /* User card */
  .u-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; flex-wrap: wrap; }
  .u-head .avatar { width: 52px; height: 52px; border-radius: 50%; background: var(--accent-soft); color: var(--accent); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 1.3em; flex: none; }
  .u-head h1 { margin: 0 0 4px; font-size: 1.6rem; }
  .u-head .badges { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
  .kv { display: grid; grid-template-columns: auto 1fr; gap: 6px 14px; font-size: .92em; }
  .kv dt { color: var(--text-50); } .kv dd { margin: 0; }
  .note { background: var(--surface-2); border-radius: 12px; padding: 10px 12px; }
  .note small { color: var(--text-35); display: block; margin-top: 4px; }
  .linkish { background: none; color: var(--danger); padding: 0; border: 0; font-size: .85em; cursor: pointer; font-weight: 500; }
  .linkish:hover { background: none; color: var(--danger); text-decoration: underline; }
  /* Product analytics card: styles spliced in from admin_product_card.CARD_CSS below */
  __PRODUCT_CARD_CSS__
  /* Info box */
  .info-box { background: var(--surface-2); padding: 1rem 1.2rem; border-radius: 14px; margin: 1rem 0; font-size: 0.92em; line-height: 1.65; color: var(--text-70); }
  .status-pill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px 2px; border-radius: 999px; font-size: .82em; font-weight: 600; background: var(--surface-2); color: var(--text-70); line-height: 1.3; }
  .status-pill i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; display: inline-block; }
  .status-pill.ok { color: var(--ok); } .status-pill.warn { color: var(--warn); } .status-pill.bad { color: var(--danger); } .status-pill.info { color: var(--info); }
  .info-box code { background: var(--accent-soft); color: var(--text); padding: 2px 6px; border-radius: 6px; font-size: 0.9em; }

  /* Flash / misc */
  .flash { padding: .7rem 1rem; border-radius: 12px; margin: .75rem 0; font-size: .9em; }
  .flash-ok { background: var(--ok-bg); color: var(--ok); }
  .flash-err { background: var(--danger-bg); color: var(--danger); }
  .meta { color: var(--text-50); font-size: .85em; }

  /* Responsive */
  @media (max-width: 900px) {
    body { padding: 0 1rem 1.5rem; }
    .header { padding-top: 12px; }
    .nav { order: 3; width: 100%; }
    .header .search-form input { width: 140px; }
    .chart-row { flex-direction: column; }
    .chart-box { flex: auto; width: 100%; }
    th, td { padding: 6px 8px; font-size: 0.8em; }
  }
</style></head><body>
<div class="header">
  <a href="/admin/" class="brand"><svg viewBox="0 0 59.9939 59.9939" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M40.0775 0.143134C41.8887 -1.66229 38.9564 14.0991 42.0393 19.9667C43.4025 22.5615 60.1717 29.2646 59.9925 30.0771C59.771 31.0819 21.8522 41.1943 22.1242 41.6683C22.4869 42.3002 28.6817 44.3039 28.8939 45.6943C29.106 47.0846 19.404 60.0837 19.132 59.9935C18.86 59.9032 20.7311 43.4375 17.6504 39.646C14.5697 35.8546 0.9496 30.6496 0.0598437 29.8967C-1.75358 28.362 38.2546 18.8044 37.5056 18.2516C34.6925 16.1753 30.7019 15.6699 30.8288 13.7742C30.9195 12.4201 39.6462 0.572985 40.0775 0.143134Z" fill="url(#blast_star_g)"/><defs><linearGradient id="blast_star_g" x1="41.1656" y1="-18.1821" x2="11.1191" y2="67.9303" gradientUnits="userSpaceOnUse"><stop stop-color="%23F6F5FD"/><stop offset="0.495192" stop-color="%238B6FE6"/><stop offset="1" stop-color="%235F42B9"/></linearGradient></defs></svg>Бласт<small>админ</small></a>
  <nav class="nav">
    <a href="/admin/" data-nav="/admin/">Обзор</a>
    <a href="/admin/clients" data-nav="/admin/clients /admin/users">Клиенты</a>
    <a href="/admin/subscriptions" data-nav="/admin/subscriptions">Подписки</a>
    <a href="/admin/sources" data-nav="/admin/sources">Источники</a>
    <a href="/admin/broadcasts" data-nav="/admin/broadcasts">Рассылки</a>
    <a href="/admin/jobs" data-nav="/admin/jobs">Джобы</a>
    <a href="/admin/render-nodes" data-nav="/admin/render-nodes">Рендер</a>
    <a href="/admin/partners" data-nav="/admin/partners">Партнёры</a>
    <details>
      <summary>Ещё ▾</summary>
      <div class="menu">
        <a href="/admin/tiers">Тарифы</a>
        <a href="/admin/lifecycle">Триггеры</a>
        <a href="/admin/users">Пользователи</a>
        <a href="/admin/activity">Активность</a>
        <a href="/admin/transactions">Транзакции</a>
        <a href="/admin/payments">Платежи</a>
        <a href="/admin/runs">Прогоны</a>
        <a href="/admin/ops">Ops</a>
        <a href="/admin/season">Сезон</a>
        <a href="/admin/assets/" target="_blank" rel="noopener noreferrer">Ассеты ↗</a>
        <a href="/admin/llm-workers">LLM-воркеры</a>
        <a href="/admin/runtime-config">Runtime config</a>
        <a href="/admin/audit">Аудит</a>
        <a href="/admin/obs/grafana/" target="_blank" rel="noopener noreferrer">Grafana ↗</a>
      </div>
    </details>
  </nav>
  <form class="search-form" action="/admin/users" method="get">
    <input type="text" name="q" placeholder="Найти клиента…">
    <button type="submit">Найти</button>
  </form>
</div>
<script>
(function(){
  var path = location.pathname.replace(/\\/+$/, '/') || '/admin/';
  var links = document.querySelectorAll('.nav a[data-nav]');
  var best = null;
  var bestLen = 0;
  links.forEach(function(a){ a.getAttribute('data-nav').split(' ').forEach(function(p){ if (path === p || (p !== '/admin/' && path.indexOf(p) === 0)) { if (p.length > bestLen) { best = a; bestLen = p.length; } } }); });
  if (best) best.classList.add('active');
  document.addEventListener('click', function(e){ document.querySelectorAll('.nav details[open]').forEach(function(d){ if (!d.contains(e.target)) d.removeAttribute('open'); }); });
})();
</script>
"""
_BASE_HEAD = _BASE_HEAD.replace("  __PRODUCT_CARD_CSS__", admin_product_card.CARD_CSS)

_BASE_FOOT = "</body></html>"


def _page(title: str, body: str, back: "tuple[str, str] | list[tuple[str, str]] | None" = None) -> str:
    """Wrap a page. `back` = (label, href) or a list of them — rendered as one
    breadcrumb trail above the heading, so leaving a sub-section always looks
    and sits the same: crumbs → title → content."""
    crumbs = ""
    if back:
        items = [back] if isinstance(back, tuple) else list(back)
        crumbs = '<nav class="crumbs">' + '<span class="sep">/</span>'.join(
            f'<a href="{html_mod.escape(href, quote=True)}">{html_mod.escape(label)}</a>' for label, href in items
        ) + "</nav>"
    heading = f"<h1>{title}</h1>" if title else ""
    return f"{_BASE_HEAD}{crumbs}{heading}{body}{_BASE_FOOT}"


def _stage_label(stage: str) -> str:
    return _STAGE_LABELS.get(stage, stage)


def _event_label(event: str) -> str:
    return _EVENT_LABELS.get(event, event)


def _llm_workers_runtime_warnings(workers: dict[str, dict[str, object]]) -> list[str]:
    enabled_types = 0
    useful_types = 0

    for row in workers.values():
        enabled = bool(row.get("enabled", False))
        weight = int(row.get("weight", 0) or 0)
        max_inflight = int(row.get("max_inflight", 0) or 0)
        if enabled:
            enabled_types += 1
        if enabled and weight > 0 and max_inflight > 0:
            useful_types += 1

    warnings: list[str] = []
    if enabled_types == 0:
        warnings.append("no_enabled_types: все worker types выключены, admission недоступен")
    elif useful_types == 0:
        warnings.append(
            "zero_useful_weight: нет ни одного enabled worker с weight > 0 и max_inflight > 0"
        )
    return warnings


def _pagination_html(page: int, total_pages: int, base_url: str = "?") -> str:
    """Generate pagination links."""
    if total_pages <= 1:
        return ""
    parts = ['<div class="pagination">']
    if page > 1:
        parts.append(f'<a href="{base_url}page={page - 1}">&laquo;</a>')
    start = max(1, page - 3)
    end = min(total_pages, page + 3)
    if start > 1:
        parts.append(f'<a href="{base_url}page=1">1</a>')
        if start > 2:
            parts.append('<span>...</span>')
    for p in range(start, end + 1):
        if p == page:
            parts.append(f'<span class="current">{p}</span>')
        else:
            parts.append(f'<a href="{base_url}page={p}">{p}</a>')
    if end < total_pages:
        if end < total_pages - 1:
            parts.append('<span>...</span>')
        parts.append(f'<a href="{base_url}page={total_pages}">{total_pages}</a>')
    if page < total_pages:
        parts.append(f'<a href="{base_url}page={page + 1}">&raquo;</a>')
    parts.append('</div>')
    return "".join(parts)


def _seconds_to_age(seconds: int) -> str:
    sec = max(0, int(seconds))
    if sec < 60:
        return f"{sec} с"
    mins, _rem = divmod(sec, 60)
    if mins < 60:
        return f"{mins} мин"
    hours, mins = divmod(mins, 60)
    if hours < 24:
        return f"{hours} ч {mins} мин" if mins else f"{hours} ч"
    days, hours = divmod(hours, 24)
    return f"{days} д {hours} ч" if hours else f"{days} д"


def _runtime_dt_text(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        try:
            return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            return str(value)
    return html_mod.escape(str(value))


def _compact_runtime_text(value: object, *, limit: int = 160) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    if len(text) <= limit:
        return html_mod.escape(text)
    return html_mod.escape(text[: max(1, limit - 1)].rstrip() + "…")


def _job_admin_link(job_id: object) -> str:
    jid = str(job_id or "").strip()
    if not jid:
        return "—"
    jid_esc = html_mod.escape(jid)
    return f"<a href='/admin/jobs/{jid_esc}'><code>{jid_esc}</code></a>"


def _project_chat_id(project_id: str) -> int | None:
    raw = str(project_id or "").strip()
    if not raw.startswith("tg-"):
        return None
    part = raw[3:].split("-", 1)[0].strip()
    if not part.isdigit():
        return None
    try:
        return int(part)
    except Exception:
        return None


def _query_int(
    request: Request,
    name: str,
    *,
    default: int,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    raw = str(request.query_params.get(name, str(default))).strip()
    try:
        value = int(raw or str(default))
    except Exception:
        value = int(default)
    if min_value is not None:
        value = max(int(min_value), value)
    if max_value is not None:
        value = min(int(max_value), value)
    return value


def _payment_status_rank(status: str) -> int:
    st = str(status or "").strip().upper()
    ranks = {
        "NEW": 0,
        "FORM_SHOWED": 1,
        "AUTHORIZED": 2,
        "CONFIRMED": 3,
        "REJECTED": 4,
        "REVERSED": 4,
        "REFUNDED": 4,
        "PARTIAL_REFUNDED": 4,
        "DEADLINE_EXPIRED": 4,
        "CANCELED": 4,
    }
    return int(ranks.get(st, -1))


def _should_apply_payment_status_update(current_status: str, incoming_status: str) -> bool:
    cur = str(current_status or "").strip().upper()
    inc = str(incoming_status or "").strip().upper()
    if not inc:
        return False
    if not cur:
        return True
    if cur == inc:
        return True
    cur_rank = _payment_status_rank(cur)
    inc_rank = _payment_status_rank(inc)
    if cur_rank >= 0 and inc_rank >= 0 and inc_rank < cur_rank:
        return False
    return True


def build_app(
    credits_db: "CreditsDB",
    state_store: "StateStore",
    settings: "Settings",
    tbank_client: "TBankClient | None" = None,
    bot_ref: "list | None" = None,
    bot_app: "Any | None" = None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None)
    security = HTTPBasic()

    # Self-hosted brand fonts (landing/fonts), reused as-is by both /admin and
    # /partner so the whole cabinet reads as one product, not a bolt-on.
    #
    # Mounted under BOTH prefixes on purpose: nginx puts Basic Auth on
    # /admin/, so a partner page pulling its fonts from /admin/static would
    # get a 401 + WWW-Authenticate per font file and the browser would pop a
    # credential dialog on every page load. Partner pages must stay entirely
    # within /partner/.
    fonts_dir = Path(__file__).resolve().parents[2] / "landing" / "fonts"
    if fonts_dir.is_dir():
        app.mount("/admin/static/fonts", StaticFiles(directory=str(fonts_dir)), name="admin_fonts")
        app.mount("/partner/static/fonts", StaticFiles(directory=str(fonts_dir)), name="partner_fonts")

    from . import partner_panel
    app.include_router(partner_panel.build_router(credits_db, state_store, settings))

    runtime_store: GenerationRuntimeStore | None = None
    try:
        pool_getter = getattr(credits_db, "_pool_or_fail", None)
        if callable(pool_getter):
            runtime_store = GenerationRuntimeStore(pool_getter())
    except Exception as e:
        log.warning("admin_panel runtime store unavailable err=%s", e)

    def _check_auth(creds: HTTPBasicCredentials = Depends(security)) -> str:
        if not secrets.compare_digest(creds.password.encode(), settings.admin_panel_password.encode()):
            raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
        return creds.username

    async def _orchestrator_get_llm_workers() -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{base}/llm-workers")
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator GET /llm-workers failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator GET /llm-workers returned non-object: {data!r}")
        return data

    async def _orchestrator_put_llm_workers(payload: dict) -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.put(f"{base}/llm-workers", json=payload)
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator PUT /llm-workers failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator PUT /llm-workers returned non-object: {data!r}")
        return data

    async def _orchestrator_get_active_jobs(*, min_age_seconds: int, limit: int) -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        params = {
            "min_age_seconds": max(0, int(min_age_seconds)),
            "limit": max(1, min(int(limit), 500)),
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(f"{base}/jobs/active", params=params)
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator GET /jobs/active failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator GET /jobs/active returned non-object: {data!r}")
        return data

    async def _orchestrator_kill_job(*, job_id: str, reason: str) -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        jid = str(job_id or "").strip()
        if not jid:
            raise RuntimeError("job_id is empty")
        payload = {"reason": " ".join(str(reason or "").split()).strip() or "stuck_job_manual_kill"}
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(f"{base}/jobs/{jid}/kill", json=payload)
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator POST /jobs/{jid}/kill failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator POST /jobs/{jid}/kill returned non-object: {data!r}")
        return data

    async def _orchestrator_requeue_job(*, job_id: str, reason: str, llm_worker_type: str = "") -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        jid = str(job_id or "").strip()
        if not jid:
            raise RuntimeError("job_id is empty")
        payload: dict[str, Any] = {
            "reason": " ".join(str(reason or "").split()).strip() or "admin_requeue",
        }
        selected_worker = str(llm_worker_type or "").strip()
        if selected_worker:
            payload["llm_worker_type"] = selected_worker
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(f"{base}/jobs/{jid}/requeue", json=payload)
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator POST /jobs/{jid}/requeue failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator POST /jobs/{jid}/requeue returned non-object: {data!r}")
        return data

    async def _orchestrator_get_job(*, job_id: str) -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        jid = str(job_id or "").strip()
        if not jid:
            raise RuntimeError("job_id is empty")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{base}/jobs/{jid}")
        if resp.status_code == 404:
            return {}
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator GET /jobs/{jid} failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator GET /jobs/{jid} returned non-object: {data!r}")
        return data

    async def _orchestrator_enqueue_alignment_smoke(payload: dict) -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        request_payload = dict(payload)
        auth_nonce = secrets.token_urlsafe(32)
        request_payload["auth_nonce"] = auth_nonce
        auth_created = await state_store.redis.set(
            alignment_smoke_authorization_key(auth_nonce),
            alignment_smoke_authorization_digest(request_payload),
            ex=AUTHORIZATION_TTL_S,
            nx=True,
        )
        if not auth_created:
            raise RuntimeError("failed to create alignment smoke authorization")
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(f"{base}/alignment-smoke", json=request_payload)
        if resp.status_code >= 300:
            raise RuntimeError(
                f"orchestrator POST /alignment-smoke failed: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(
                f"orchestrator POST /alignment-smoke returned non-object: {data!r}"
            )
        return data

    async def _orchestrator_get_metrics() -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{base}/metrics")
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator GET /metrics failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator GET /metrics returned non-object: {data!r}")
        return data

    async def _orchestrator_get_runtime_config() -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{base}/runtime-config")
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator GET /runtime-config failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator GET /runtime-config returned non-object: {data!r}")
        return data

    async def _orchestrator_put_runtime_config(payload: dict) -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.put(f"{base}/runtime-config", json=payload)
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator PUT /runtime-config failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator PUT /runtime-config returned non-object: {data!r}")
        return data

    async def _safe_get_metrics() -> dict:
        try:
            return await _orchestrator_get_metrics()
        except Exception:
            return {}

    async def _safe_get_windows_nodes() -> dict:
        try:
            return await _orchestrator_get_windows_nodes()
        except Exception:
            return {}

    async def _safe_get_llm_workers() -> dict:
        try:
            return await _orchestrator_get_llm_workers()
        except Exception:
            return {}

    async def _telegram_get_webhook_info() -> dict:
        token = str(settings.tg_bot_token or "").strip()
        if not token:
            return {}
        async with httpx.AsyncClient(timeout=15.0, **_telegram_proxy_kwargs()) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getWebhookInfo")
        if resp.status_code >= 300:
            raise RuntimeError(f"telegram getWebhookInfo failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"telegram getWebhookInfo returned non-object: {data!r}")
        if not data.get("ok"):
            raise RuntimeError(f"telegram getWebhookInfo not ok: {data!r}")
        result = data.get("result")
        return result if isinstance(result, dict) else {}

    async def _safe_get_webhook_info() -> dict:
        if str(getattr(settings, "tg_delivery_mode", "") or "").strip().lower() != "webhook":
            return {}
        try:
            return await _telegram_get_webhook_info()
        except Exception:
            return {}

    async def _safe_get_runtime_stats() -> dict:
        if runtime_store is None:
            return {}
        try:
            return await runtime_store.get_runtime_stats(surface="public")
        except Exception as e:
            return {"error": str(e)}

    def _telegram_proxy_kwargs() -> dict[str, str]:
        proxy = str(getattr(settings, "tg_file_proxy_url", "") or "").strip()
        return {"proxy": proxy} if proxy else {}

    async def _send_alert_telegram_message(text: str) -> dict:
        token = str(getattr(settings, "alert_telegram_bot_token", "") or "").strip()
        chat_id = str(getattr(settings, "alert_telegram_chat_id", "") or "").strip()
        if not token:
            raise RuntimeError("ALERT_TELEGRAM_BOT_TOKEN is empty")
        if not chat_id:
            raise RuntimeError("ALERT_TELEGRAM_CHAT_ID is empty")
        msg = str(text or "").strip()
        if not msg:
            raise RuntimeError("alert text is empty")
        async with httpx.AsyncClient(timeout=15.0, **_telegram_proxy_kwargs()) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": msg,
                    "disable_web_page_preview": True,
                },
            )
        if resp.status_code >= 300:
            raise RuntimeError(f"telegram sendMessage failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict) or not data.get("ok"):
            raise RuntimeError(f"telegram sendMessage not ok: {data!r}")
        return data

    async def _send_alert_smoke(*, actor: str) -> dict:
        text = "\n".join(
            [
                "Blast admin alert smoke",
                f"actor: {actor or 'admin'}",
                f"ts: {datetime.now(timezone.utc).isoformat()}",
                "scope: admin-only check, no user job created",
            ]
        )
        return await _send_alert_telegram_message(text)

    async def _send_friendly_error_smoke(*, actor: str) -> dict:
        text = "\n".join(
            [
                "Blast friendly-error smoke",
                f"actor: {actor or 'admin'}",
                f"ts: {datetime.now(timezone.utc).isoformat()}",
                "user_text: Мы не смогли корректно собрать видео. Деньги за эту попытку не списаны, а команда уже получила технические детали.",
                "tech_error_code: friendly_error_smoke",
                "tech_details: simulated traceback stays in ops alert only",
                "scope: admin-only check, no user job created",
            ]
        )
        return await _send_alert_telegram_message(text)

    async def _orchestrator_get_windows_nodes() -> dict:
        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{base}/windows-nodes")
        if resp.status_code >= 300:
            raise RuntimeError(f"orchestrator GET /windows-nodes failed: {resp.status_code} {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"orchestrator GET /windows-nodes returned non-object: {data!r}")
        return data

    donor_restart_state: dict[str, object] = {
        "run_id": 0,
        "running": False,
        "status": "idle",
        "started_at": 0.0,
        "finished_at": 0.0,
        "initiator": "",
        "summary": "",
        "last_error": "",
        "command": "",
        "log_tail": deque(maxlen=300),
    }
    donor_restart_lock = asyncio.Lock()

    def _cfg_bool(name: str, default: bool) -> bool:
        return bool(getattr(settings, name, default))

    def _cfg_str(name: str, default: str = "") -> str:
        return str(getattr(settings, name, default) or "").strip()

    def _cfg_int(name: str, default: int) -> int:
        try:
            return int(getattr(settings, name, default))
        except Exception:
            return int(default)

    def _cfg_float(name: str, default: float) -> float:
        try:
            return float(getattr(settings, name, default))
        except Exception:
            return float(default)

    def _donor_restart_enabled() -> bool:
        return _cfg_bool("admin_panel_enable_donor_restart", False)

    def _build_donor_restart_command() -> list[str]:
        if not _donor_restart_enabled():
            raise RuntimeError("ADMIN_PANEL_ENABLE_DONOR_RESTART is disabled")

        node_host = _cfg_str("windows_donor_host")
        node_user = _cfg_str("windows_donor_user", "Administrator") or "Administrator"
        node_password = _cfg_str("windows_donor_password")
        test_node_url = _cfg_str("windows_donor_url", "http://85.239.48.31:8000")
        orchestrator_url = _cfg_str("orchestrator_public_url")
        canary_audio_url = _cfg_str("windows_donor_canary_audio_s3_url")
        canary_mode = _cfg_str("windows_donor_canary_mode", "with_gemini") or "with_gemini"
        llm_worker_type = _cfg_str("windows_donor_llm_worker_type", "openrouter")

        if not node_host:
            raise RuntimeError("WINDOWS_DONOR_HOST is empty")
        if not node_password:
            raise RuntimeError("WINDOWS_DONOR_PASSWORD is empty")
        if not test_node_url:
            raise RuntimeError("WINDOWS_DONOR_URL is empty")
        if not orchestrator_url:
            raise RuntimeError("ORCHESTRATOR_PUBLIC_URL is empty")
        if not canary_audio_url:
            raise RuntimeError("WINDOWS_DONOR_CANARY_AUDIO_S3_URL is empty")
        if canary_mode not in {"with_gemini", "no_gemini"}:
            raise RuntimeError("WINDOWS_DONOR_CANARY_MODE must be with_gemini|no_gemini")

        script_path = (Path(__file__).resolve().parents[2] / "scripts" / "windows_node_rollout.py").resolve()
        if not script_path.exists():
            raise RuntimeError(f"windows rollout script not found: {script_path}")

        cmd = [
            sys.executable,
            str(script_path),
            "--node-host",
            node_host,
            "--node-user",
            node_user,
            "--node-password",
            node_password,
            "--test-node-url",
            test_node_url,
            "--orchestrator-url",
            orchestrator_url,
            "--canary-audio-s3-url",
            canary_audio_url,
            "--canary-mode",
            canary_mode,
            "--health-timeout-sec",
            str(max(30, _cfg_int("windows_donor_health_timeout_s", 180))),
            "--health-poll-sec",
            str(max(1, _cfg_int("windows_donor_health_poll_s", 2))),
            "--canary-timeout-sec",
            str(max(60, _cfg_int("windows_donor_canary_timeout_s", 1800))),
            "--canary-poll-sec",
            str(max(1.0, _cfg_float("windows_donor_canary_poll_s", 5.0))),
        ]
        if llm_worker_type:
            cmd.extend(["--llm-worker-type", llm_worker_type])
        if _cfg_bool("windows_donor_start_afterfx", True):
            cmd.append("--start-afterfx")
        if _cfg_bool("windows_donor_kill_afterfx_first", True):
            cmd.append("--kill-afterfx-first")
        if _cfg_bool("windows_donor_skip_restart", False):
            cmd.append("--skip-restart")
        return cmd

    def _command_display(cmd: list[str]) -> str:
        masked: list[str] = []
        i = 0
        while i < len(cmd):
            part = str(cmd[i])
            if part == "--node-password" and (i + 1) < len(cmd):
                masked.extend([part, "***"])
                i += 2
                continue
            masked.append(part)
            i += 1
        return " ".join(shlex.quote(x) for x in masked)

    async def _donor_restart_snapshot() -> dict[str, object]:
        async with donor_restart_lock:
            snap: dict[str, object] = {}
            for k, v in donor_restart_state.items():
                if k == "log_tail":
                    snap[k] = list(v) if isinstance(v, deque) else []
                else:
                    snap[k] = v
            return snap

    async def _run_donor_restart_background(*, run_id: int, actor: str, cmd: list[str]) -> None:
        async with donor_restart_lock:
            donor_restart_state["running"] = True
            donor_restart_state["status"] = "running"
            donor_restart_state["started_at"] = float(time.time())
            donor_restart_state["finished_at"] = 0.0
            donor_restart_state["initiator"] = str(actor or "")
            donor_restart_state["summary"] = "running"
            donor_restart_state["last_error"] = ""
            donor_restart_state["command"] = _command_display(cmd)
            tail = donor_restart_state.get("log_tail")
            if isinstance(tail, deque):
                tail.clear()
                tail.append(f"[run {run_id}] starting")
            donor_restart_state["run_id"] = int(run_id)

        rc = -1
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.readline()
                if not chunk:
                    break
                line = chunk.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                log.info("donor_restart run_id=%s line=%s", run_id, line)
                async with donor_restart_lock:
                    tail = donor_restart_state.get("log_tail")
                    if isinstance(tail, deque):
                        tail.append(line)
            rc = int(await proc.wait())
        except Exception as e:
            async with donor_restart_lock:
                donor_restart_state["running"] = False
                donor_restart_state["status"] = "failed"
                donor_restart_state["finished_at"] = float(time.time())
                donor_restart_state["summary"] = "failed_to_start"
                donor_restart_state["last_error"] = repr(e)
                tail = donor_restart_state.get("log_tail")
                if isinstance(tail, deque):
                    tail.append(f"exception: {e!r}")
            return

        async with donor_restart_lock:
            donor_restart_state["running"] = False
            donor_restart_state["finished_at"] = float(time.time())
            if rc == 0:
                donor_restart_state["status"] = "succeeded"
                donor_restart_state["summary"] = "completed"
                donor_restart_state["last_error"] = ""
            else:
                donor_restart_state["status"] = "failed"
                donor_restart_state["summary"] = f"failed_rc={rc}"
                donor_restart_state["last_error"] = f"return_code={rc}"
            tail = donor_restart_state.get("log_tail")
            if isinstance(tail, deque):
                tail.append(f"[run {run_id}] finished rc={rc}")

    # ── Dashboard ─────────────────────────────────────────────────────

    @app.get("/admin/", response_class=HTMLResponse)
    async def dashboard(request: Request, _user: str = Depends(_check_auth)) -> str:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td

        # ── Period selection via query params ──
        now_utc = _dt.now(_tz.utc)
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        period_param = str(request.query_params.get("period", "")).strip()
        channel = str(request.query_params.get("channel", "bot")).strip().lower()
        if channel not in {"site", "bot", "all"}:
            channel = "bot"
        date_from_param = str(request.query_params.get("date_from", "")).strip()
        date_to_param = str(request.query_params.get("date_to", "")).strip()

        _PERIOD_PRESETS = {
            "1d": ("Сегодня", today_start, now_utc),
            "7d": ("7 дней", now_utc - _td(days=7), now_utc),
            "30d": ("30 дней", now_utc - _td(days=30), now_utc),
            "90d": ("90 дней", now_utc - _td(days=90), now_utc),
            "all": ("Всё время", _dt(2020, 1, 1, tzinfo=_tz.utc), now_utc),
        }

        # Determine effective range
        if date_from_param and date_to_param:
            # Custom range from form
            try:
                period_from = _dt.strptime(date_from_param, "%Y-%m-%d").replace(tzinfo=_tz.utc)
                period_to = _dt.strptime(date_to_param, "%Y-%m-%d").replace(tzinfo=_tz.utc) + _td(days=1)
                active_period = "custom"
            except ValueError:
                period_from, period_to, active_period = now_utc - _td(days=30), now_utc, "30d"
        elif period_param in _PERIOD_PRESETS:
            _, period_from, period_to = _PERIOD_PRESETS[period_param]
            active_period = period_param
        else:
            active_period = "30d"
            _, period_from, period_to = _PERIOD_PRESETS["30d"]

        # Revenue chart: weekly vs monthly toggle (default "month").
        revenue_bucket = str(request.query_params.get("rev_bucket", "month")).strip().lower()
        if revenue_bucket not in {"week", "month"}:
            revenue_bucket = "month"
        revenue_periods = 12 if revenue_bucket == "month" else 12

        # Users chart: same toggle pattern.
        users_bucket = str(request.query_params.get("users_bucket", "month")).strip().lower()
        if users_bucket not in {"week", "month"}:
            users_bucket = "month"

        ratings_raw, funnel_raw, stage_counts, users, recent, payments_summary, period_stats_row, metrics_data, windows_nodes_data, llm_workers_data, webhook_info, revenue_series, subs_summary, users_data, product_source, spend_rows = await asyncio.gather(
            credits_db.rating_distribution(),
            credits_db.funnel_reach_counts(),
            state_store.list_stage_counts(),
            credits_db.list_users(limit=10),
            credits_db.get_activity(limit=10),
            credits_db.payments_status_summary(),
            credits_db.period_stats_range(period_from, period_to),
            _safe_get_metrics(),
            _safe_get_windows_nodes(),
            _safe_get_llm_workers(),
            _safe_get_webhook_info(),
            credits_db.revenue_timeseries(bucket=revenue_bucket, periods=revenue_periods),
            credits_db.subscriptions_summary(),
            credits_db.users_timeseries(bucket=users_bucket),
            credits_db.product_metrics_source(channel, period_from, period_to),
            credits_db.list_marketing_spend(),
        )

        product_source.subscriptions = subs_summary if isinstance(subs_summary, dict) else {}
        try:
            active_days = int(request.query_params.get("active", product_metrics.ACTIVE_WINDOW_DAYS))
        except ValueError:
            active_days = product_metrics.ACTIVE_WINDOW_DAYS
        product_metrics_data = product_metrics.compute(
            product_source, channel=channel, date_from=period_from, date_to=period_to, now=now_utc,
            active_days=active_days,
        )
        if active_period == "custom":
            period_label = f"{period_from:%d.%m.%Y} — {(period_to - _td(days=1)):%d.%m.%Y}"
        else:
            period_label = _PERIOD_PRESETS[active_period][0].lower()
        spend_flash_param = str(request.query_params.get("spend", "")).strip()
        spend_flash = (
            '<div class="flash flash-ok">Расходы сохранены</div>' if spend_flash_param == "ok"
            else f'<div class="flash flash-err">{html_mod.escape(spend_flash_param)}</div>' if spend_flash_param else ""
        )
        product_card = admin_product_card.render_product_card(
            product_metrics_data,
            channel=channel,
            active_period=active_period,
            period_label=period_label,
            spend_rows=spend_rows if isinstance(spend_rows, list) else [],
            spend_flash=spend_flash,
        )

        # ── Rating distribution (3 buckets matching data we log: low/5-6/7-10) ──
        rating_map = {r["rating"]: r["count"] for r in ratings_raw}
        rating_keys = ["low", "mid_low", "high"]
        rating_chart_labels = json.dumps([_RATING_LABELS[k] for k in rating_keys])
        rating_chart_data = json.dumps([int(rating_map.get(k, 0)) for k in rating_keys])
        rating_chart_colors = json.dumps([_RATING_COLORS[k] for k in rating_keys])
        total_ratings = sum(int(rating_map.get(k, 0)) for k in rating_keys)

        # ── Funnel grouped into 3 stage mini-cards + other ──
        funnel_map = {r["event"]: r["count"] for r in funnel_raw}

        def _funnel_group_html(events: list[str], color: str) -> str:
            counts = [(ev, int(funnel_map.get(ev, 0))) for ev in events]
            base = counts[0][1] if counts else 0
            base = base if base > 0 else 1
            local_max = max((c for _, c in counts), default=1) or 1
            out = ""
            for idx, (ev, cnt) in enumerate(counts):
                bar_pct = max(15, cnt / local_max * 100) if local_max else 15
                conv_from_start = cnt / base * 100
                if idx == 0:
                    step_lbl = "—"
                else:
                    prev = counts[idx - 1][1] or 1
                    step_lbl = f"{cnt / prev * 100:.0f}% от пред."
                out += (
                    f'<div class="pm-funnel-row" title="{step_lbl}"><div>{_event_label(ev)}</div>'
                    f'<div class="bar"><i style="width:{max(1.0, conv_from_start):.0f}%;background:{color}"></i></div>'
                    f'<div class="n">{cnt}<span>{conv_from_start:.0f}%</span></div></div>'
                )
            return out or '<p style="color:#8d86ad">Нет данных</p>'

        funnel_cards_html = ""
        for group_title, group_color, group_events in _FUNNEL_GROUPS:
            funnel_cards_html += (
                f'<div class="pm-panel" style="flex:1;min-width:280px;gap:10px">'
                f'<div class="ph"><b>{group_title}</b></div>'
                f'{_funnel_group_html(list(group_events), group_color)}'
                f'</div>'
            )

        other_rows = ""
        for ev in _FUNNEL_OTHER_EVENTS:
            cnt = int(funnel_map.get(ev, 0))
            other_rows += (
                f'<div class="stage-chip"><div class="count">{cnt}</div>'
                f'<div class="label">{_event_label(ev)}</div></div>'
            )

        # ── Current stage snapshot from indexed Redis counters ──
        stage_html = ""
        for stage, cnt in sorted(stage_counts.items(), key=lambda x: -x[1]):
            label = _stage_label(stage)
            stage_html += f'<div class="stage-chip"><div class="count">{cnt}</div><div class="label">{label}</div></div>'

        # ── Recent users ──
        user_rows = ""
        for u in users:
            badge = "badge-ok" if u["credits"] > 0 else "badge-zero"
            uname = f"@{u['username']}" if u["username"] else str(u["tg_id"])
            user_rows += (
                f"<tr><td><a href='/admin/users/{u['tg_id']}'>{uname}</a></td>"
                f"<td>{u['tg_id']}</td>"
                f"<td><span class='badge {badge}'>{u['credits']}</span></td>"
                f"<td>{u['updated_at']}</td></tr>"
            )

        # ── Recent activity ──
        act_rows = ""
        for a in recent:
            act_rows += (
                f"<tr><td><a href='/admin/users/{a['tg_id']}'>{a['tg_id']}</a></td>"
                f"<td>{_event_label(a['event'])}</td>"
                f"<td>{a['detail']}</td>"
                f"<td>{a['created_at']}</td></tr>"
            )

        # ── Period pills HTML ──
        period_pills_html = ""
        for _pk, (_plbl, _, _) in _PERIOD_PRESETS.items():
            period_pills_html += f'<a href="/admin/?period={_pk}&channel={channel}" class="{"on" if active_period == _pk else ""}">{_plbl}</a>'
        period_custom_badge = f'<span class="badge badge-stage">{html_mod.escape(date_from_param)} — {html_mod.escape(date_to_param)}</span>' if active_period == "custom" else ""
        period_date_from_val = date_from_param or period_from.strftime("%Y-%m-%d")
        period_date_to_val = date_to_param or (period_to - _td(days=1)).strftime("%Y-%m-%d")

        # ── Queue / jobs metrics card ──
        job_counts = metrics_data.get("job_status_counts") or {} if isinstance(metrics_data, dict) else {}
        celery_workers = metrics_data.get("workers") or {} if isinstance(metrics_data, dict) else {}
        llm_inflight = metrics_data.get("llm_inflight_by_worker_type") or {} if isinstance(metrics_data, dict) else {}
        if job_counts:
            q_new = int(job_counts.get("NEW", 0))
            q_queued = int(job_counts.get("QUEUED", 0))
            q_running = int(job_counts.get("RUNNING", 0))
            q_succeeded = int(job_counts.get("SUCCEEDED", 0))
            q_failed = int(job_counts.get("FAILED", 0))

            worker_rows = ""
            for wname, wdata in celery_workers.items():
                if isinstance(wdata, dict):
                    worker_rows += f"<tr><td>{html_mod.escape(wname)}</td><td>{wdata.get('active', 0)}</td><td>{wdata.get('reserved', 0)}</td></tr>"

            llm_chips = ""
            for lt, cnt in llm_inflight.items():
                llm_chips += f'<div class="stage-chip"><div class="count">{cnt}</div><div class="label">{lt}</div></div>'

            metrics_card = f"""
            <div class="card">
            <div class="toolbar"><h2 style="margin:0">Очередь генераций</h2><a href="/admin/jobs?min_age_seconds=0">все задачи →</a></div>
            <div class="stage-grid" style="margin-top:12px">
              <div class="stage-chip"><div class="count">{q_new}</div><div class="label">новых</div></div>
              <div class="stage-chip"><div class="count" style="color:#f0b04a">{q_queued}</div><div class="label">в очереди</div></div>
              <div class="stage-chip"><div class="count" style="color:#5aa9ea">{q_running}</div><div class="label">выполняются</div></div>
              <div class="stage-chip"><div class="count" style="color:#4ecf8a">{q_succeeded}</div><div class="label">готово</div></div>
              <div class="stage-chip"><div class="count" style="color:#ef6a6a">{q_failed}</div><div class="label">с ошибкой</div></div>
            </div>
            </div>
            """
        else:
            metrics_card = ""

        effective_nodes = windows_nodes_data.get("effective_urls") or [] if isinstance(windows_nodes_data, dict) else []
        llm_rows = llm_workers_data.get("workers") or {} if isinstance(llm_workers_data, dict) else {}
        llm_worker_html = ""
        for worker_name, worker_row in llm_rows.items():
            if not isinstance(worker_row, dict):
                continue
            enabled = "on" if bool(worker_row.get("enabled")) else "off"
            inflight = int(worker_row.get("inflight", 0) or 0)
            max_inflight = int(worker_row.get("max_inflight", 0) or 0)
            llm_worker_html += (
                f"<div class='stage-chip'>"
                f"<div class='count'>{inflight}/{max_inflight}</div>"
                f"<div class='label'>{html_mod.escape(str(worker_name))}{'' if enabled == 'on' else ' · выключен'}</div>"
                f"</div>"
            )

        webhook_pending = int(webhook_info.get("pending_update_count", 0) or 0) if isinstance(webhook_info, dict) else 0
        webhook_last_error = html_mod.escape(str(webhook_info.get("last_error_message") or "").strip()) if isinstance(webhook_info, dict) else ""
        webhook_url = html_mod.escape(str(webhook_info.get("url") or "").strip()) if isinstance(webhook_info, dict) else ""
        maintenance_state = "ON" if bool(getattr(settings, "tg_maintenance_mode", False)) else "OFF"
        alert_configured = bool(str(getattr(settings, "alert_telegram_bot_token", "") or "").strip()) and bool(
            str(getattr(settings, "alert_telegram_chat_id", "") or "").strip()
        )
        webhook_ok = not webhook_last_error
        health_card = f"""
        <div class="card">
        <div class="toolbar"><h2 style="margin:0">Состояние системы</h2><a href="/admin/ops">операции →</a></div>
        <div class="stage-grid" style="margin-top:12px">
          <div class="stage-chip"><div class="count" style="color:{'var(--warn)' if maintenance_state == 'ON' else 'var(--ok)'}">{'вкл' if maintenance_state == 'ON' else 'выкл'}</div><div class="label">режим обслуживания</div></div>
          <div class="stage-chip"><div class="count" style="color:{'var(--ok)' if alert_configured else 'var(--warn)'}">{'да' if alert_configured else 'нет'}</div><div class="label">алерты в Telegram</div></div>
          <div class="stage-chip"><div class="count" style="color:{'var(--ok)' if effective_nodes else 'var(--danger)'}">{len(effective_nodes)}</div><div class="label">рендер-нод в пуле</div></div>
          <div class="stage-chip"><div class="count" style="color:{'var(--ok)' if webhook_ok else 'var(--danger)'}">{html_mod.escape(str(settings.tg_delivery_mode or 'polling'))}</div><div class="label">доставка{f' · очередь {webhook_pending}' if webhook_pending else ''}</div></div>
        </div>
        {f"<p style='color:var(--danger);margin-top:10px'>Ошибка вебхука: {webhook_last_error}</p>" if webhook_last_error else ""}
        </div>
        """

        # ── Revenue chart payload ──
        rev_labels_json = json.dumps([str(r["bucket"]) for r in revenue_series])
        rev_data_json = json.dumps([int(r["rub"]) for r in revenue_series])
        rev_bucket_lbl = (
            "Текущий месяц · по дням"
            if revenue_bucket == "month"
            else f"Последние {len(revenue_series)} недель"
        )
        rev_week_btn = (
            f'<a href="/admin/?rev_bucket=week" class="btn" '
            f'style="{"background:#f2effc;color:#07030f;font-weight:600" if revenue_bucket == "week" else "background:#1a1331;color:#8d86ad"}">Неделя</a>'
        )
        rev_month_btn = (
            f'<a href="/admin/?rev_bucket=month" class="btn" '
            f'style="{"background:#f2effc;color:#07030f;font-weight:600" if revenue_bucket == "month" else "background:#1a1331;color:#8d86ad"}">Месяц</a>'
        )

        visible_rub = int(payments_summary.get('visible_revenue_rub', 0))

        # ── Subscriptions teaser (links to /admin/subscriptions) ──
        subs_card = f"""
        <div class="card">
          <h2>Подписки <a href="/admin/subscriptions" style="font-size:0.6em">подробнее →</a></h2>
          <div class="stage-grid">
            <div class="stage-chip"><div class="count" style="color:#4ecf8a">{subs_summary.get('active_cnt', 0)}</div><div class="label">активны</div></div>
            <div class="stage-chip"><div class="count" style="color:#ef6a6a">{subs_summary.get('paused_cnt', 0)}</div><div class="label">на паузе</div></div>
            <div class="stage-chip"><div class="count">{subs_summary.get('due_today_cnt', 0)}</div><div class="label">сегодня к списанию ({int(subs_summary.get('due_today_rub', 0)):,}&nbsp;&#8381;)</div></div>
            <div class="stage-chip"><div class="count">{subs_summary.get('due_7d_cnt', 0)}</div><div class="label">7 дней ({int(subs_summary.get('due_7d_rub', 0)):,}&nbsp;&#8381;)</div></div>
            <div class="stage-chip"><div class="count" style="color:#4ecf8a">{subs_summary.get('recurrent_ok_30d', 0)}</div><div class="label">списано за 30д ({int(subs_summary.get('recurrent_revenue_30d', 0)):,}&nbsp;&#8381;)</div></div>
            <div class="stage-chip"><div class="count" style="color:#ef6a6a">{subs_summary.get('recurrent_fail_30d', 0)}</div><div class="label">фейлов за 30д</div></div>
          </div>
        </div>
        """

        # ── Users chart payload ──
        users_series = users_data.get("series", []) if isinstance(users_data, dict) else []
        users_labels_json = json.dumps([str(s["bucket"]) for s in users_series])
        users_inflow_json = json.dumps([int(s["inflow"]) for s in users_series])
        users_outflow_json = json.dumps([int(s["outflow"]) for s in users_series])
        users_total = int(users_data.get("total_users", 0) or 0) if isinstance(users_data, dict) else 0
        users_blocked = int(users_data.get("blocked_total", 0) or 0) if isinstance(users_data, dict) else 0
        users_active = int(users_data.get("active_total", 0) or 0) if isinstance(users_data, dict) else 0
        users_bucket_lbl = (
            "Текущий месяц · по дням" if users_bucket == "month"
            else f"Последние {len(users_series)} недель"
        )
        users_week_btn = (
            f'<a href="/admin/?users_bucket=week" class="btn" '
            f'style="{"background:#f2effc;color:#07030f;font-weight:600" if users_bucket == "week" else "background:#1a1331;color:#8d86ad"}">Неделя</a>'
        )
        users_month_btn = (
            f'<a href="/admin/?users_bucket=month" class="btn" '
            f'style="{"background:#f2effc;color:#07030f;font-weight:600" if users_bucket == "month" else "background:#1a1331;color:#8d86ad"}">Месяц</a>'
        )

        def _rating_row(key: str, label: str) -> str:
            cnt = int(rating_map.get(key, 0))
            pct = cnt / total_ratings * 100 if total_ratings else 0
            dot = f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:{_RATING_COLORS[key]};margin-right:6px"></span>'
            return (f'<div class="pm-kpi"><div class="l">{dot}{label}</div>'
                    f'<div class="v" style="font-size:1.6em">{pct:.0f}%</div><div class="s">{cnt}</div></div>')
        if total_ratings == 0:
            ratings_panel_html = "<p class='meta'>Оценок пока нет</p>"
        else:
            ratings_panel_html = (
                '<div style="display:flex;align-items:center;gap:22px;flex:1;min-height:0">'
                '<div class="donut"><canvas id="ratingsChart"></canvas></div>'
                '<div class="stack" style="gap:10px;flex:0 0 auto;min-width:90px">'
                + _rating_row("high", "7–10") + _rating_row("mid_low", "5–6") + _rating_row("low", "до 5")
                + "</div></div>"
            )
        users_seg = (
            '<div class="seg">'
            f'<a href="/admin/?users_bucket=week&channel={channel}&period={active_period}" class="{"on" if users_bucket == "week" else ""}">Недели</a>'
            f'<a href="/admin/?users_bucket=month&channel={channel}&period={active_period}" class="{"on" if users_bucket == "month" else ""}">Месяц</a>'
            '</div>'
        )
        rev_seg = (
            '<div class="seg">'
            f'<a href="/admin/?rev_bucket=week&channel={channel}&period={active_period}" class="{"on" if revenue_bucket == "week" else ""}">Недели</a>'
            f'<a href="/admin/?rev_bucket=month&channel={channel}&period={active_period}" class="{"on" if revenue_bucket == "month" else ""}">Месяц</a>'
            '</div>'
        )
        body = f"""
        {product_card}

        <div class="pm-grid" style="margin-top:18px">
          <div class="pm-panel" style="grid-column: span 8; gap: 12px">
            <div class="ph"><b>Выручка по времени <span>{rev_bucket_lbl.lower()}</span></b>{rev_seg}</div>
            <div style="height:230px"><canvas id="revenueChart"></canvas></div>
          </div>
          <div class="pm-panel" style="grid-column: span 4; gap: 12px">
            <div class="ph"><b>Оценки видео <span>{total_ratings} всего</span></b></div>
            {ratings_panel_html}
          </div>
          <div class="pm-panel" style="grid-column: span 8; gap: 12px">
            <div class="ph"><b>Приток и отток <span>{users_bucket_lbl.lower()} · {users_total:,} всего, {users_blocked:,} отписались</span></b>{users_seg}</div>
            <div style="height:230px"><canvas id="usersChart"></canvas></div>
          </div>
          <div class="pm-panel" style="grid-column: span 4; gap: 12px">
            <div class="ph"><b>Подписки</b><a href="/admin/subscriptions">все →</a></div>
            <div class="pm-kpis" style="grid-template-columns: repeat(2, minmax(0, 1fr))">
              <div class="pm-kpi"><div class="l">Активных</div><div class="v acc">{subs_summary.get('active_cnt', 0)}</div><div class="s">на паузе {subs_summary.get('paused_cnt', 0)}</div></div>
              <div class="pm-kpi"><div class="l">Списаний за 30 дней</div><div class="v">{subs_summary.get('recurrent_ok_30d', 0)}</div><div class="s">{int(subs_summary.get('recurrent_revenue_30d', 0)):,} ₽ · не прошло {subs_summary.get('recurrent_fail_30d', 0)}</div></div>
              <div class="pm-kpi"><div class="l">Сегодня к списанию</div><div class="v">{subs_summary.get('due_today_cnt', 0)}</div><div class="s">{int(subs_summary.get('due_today_rub', 0)):,} ₽</div></div>
              <div class="pm-kpi"><div class="l">За 7 дней</div><div class="v">{subs_summary.get('due_7d_cnt', 0)}</div><div class="s">{int(subs_summary.get('due_7d_rub', 0)):,} ₽</div></div>
            </div>
          </div>
        </div>

        <div class="pm-section" style="margin-top:26px"><b>Путь клиента</b><span>по всем событиям за всё время</span></div>
        <div style="display:flex;gap:18px;flex-wrap:wrap">
          {funnel_cards_html}
        </div>

        <div class="pm-grid" style="margin-top:18px">
          <div class="pm-panel" style="grid-column: span 7; gap: 12px">
            <div class="ph"><b>Где сейчас клиенты <span>стадия диалога в боте, live</span></b></div>
            <div class="stage-grid" style="margin:0">{stage_html if stage_html else '<p class="meta">Нет данных</p>'}</div>
          </div>
          <div class="pm-panel" style="grid-column: span 5; gap: 12px">
            <div class="ph"><b>Прочие события</b></div>
            <div class="stage-grid" style="margin:0">{other_rows or '<p class="meta">Нет данных</p>'}</div>
          </div>
        </div>

        <div class="card" style="margin-top:18px">
          <div class="toolbar">
            <h2 id="periods" style="margin:0">Сводка за период</h2>
            <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
              <div class="seg">{period_pills_html}</div>
              <form method="get" action="/admin/" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                <input type="hidden" name="channel" value="{channel}">
                <input type="date" name="date_from" value="{period_date_from_val}">
                <span class="meta">—</span>
                <input type="date" name="date_to" value="{period_date_to_val}">
                <button type="submit" class="btn-secondary">Показать</button>
              </form>
            </div>
          </div>
          <div class="table-wrap" style="margin-top:10px">
          <table>
            <tr>
              <th>Новые</th><th>Отписки</th><th>Запустили бота</th><th>Начали генерацию</th><th>Получили ролик</th><th>Ошибки генерации</th><th>Смотрели тарифы</th><th>Оплат</th><th>Выручка</th>
            </tr>
            <tr>
              <td>{int(period_stats_row.get('users_new', 0))}</td>
              <td>{int(period_stats_row.get('bot_blocked_users', 0))}</td>
              <td>{int(period_stats_row.get('starts_users', 0))}</td>
              <td>{int(period_stats_row.get('generation_started_users', 0))}</td>
              <td>{int(period_stats_row.get('generation_done_users', 0))}</td>
              <td>{int(period_stats_row.get('generation_failed_users', 0))}</td>
              <td>{int(period_stats_row.get('purchase_intent_users', 0))}</td>
              <td>{int(period_stats_row.get('paid_orders', 0))}</td>
              <td>{int(period_stats_row.get('revenue_rub', 0)):,}&nbsp;&#8381;</td>
            </tr>
          </table>
          </div>
        </div>

        {metrics_card}
        {health_card}

        <script>
        (function() {{
          const revLabels = {rev_labels_json};
          const revData = {rev_data_json};
          if (revLabels.length) {{
            new Chart(document.getElementById("revenueChart"), {{
              type: "bar",
              data: {{
                labels: revLabels,
                datasets: [{{
                  label: "Выручка (₽)",
                  data: revData,
                  backgroundColor: "#9b84ee",
                  borderRadius: 6,
                }}]
              }},
              options: {{
                responsive: true, maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{ y: {{ beginAtZero: true, grid: {{ color: "rgba(255,255,255,.06)" }} }}, x: {{ grid: {{ display: false }} }} }}
              }}
            }});
          }}
          const usersLabels = {users_labels_json};
          const usersInflow = {users_inflow_json};
          const usersOutflow = {users_outflow_json};
          if (usersLabels.length) {{
            new Chart(document.getElementById("usersChart"), {{
              type: "bar",
              data: {{
                labels: usersLabels,
                datasets: [
                  {{
                    label: "Приток",
                    data: usersInflow,
                    backgroundColor: "#4ecf8a",
                    borderRadius: 6,
                  }},
                  {{
                    label: "Отток",
                    data: usersOutflow,
                    backgroundColor: "#ef6a6a",
                    borderRadius: 6,
                  }}
                ]
              }},
              options: {{
                responsive: true, maintainAspectRatio: false,
                plugins: {{ legend: {{ position: "top", align: "end", labels: {{ boxWidth: 10, boxHeight: 10, usePointStyle: true }} }} }},
                scales: {{ y: {{ beginAtZero: true, grid: {{ color: "rgba(255,255,255,.06)" }} }}, x: {{ grid: {{ display: false }} }} }}
              }}
            }});
          }}
          {"" if total_ratings == 0 else '''
          new Chart(document.getElementById("ratingsChart"), {
            type: "doughnut",
            data: {
              labels: ''' + rating_chart_labels + ''',
              datasets: [{
                data: ''' + rating_chart_data + ''',
                backgroundColor: ''' + rating_chart_colors + ''',
                borderWidth: 3,
                borderColor: "#110b20",
              }]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              cutout: "68%",
              plugins: { legend: { display: false } }
            }
          });
          '''}
        }})();
        </script>
        """
        return _page("", body)

    # ── Operator toolkit ───────────────────────────────────────────────

    @app.post("/admin/marketing-spend")
    async def marketing_spend_save(
        request: Request,
        month: str = Form(...),
        spend_rub: int = Form(...),
        source: str = Form(""),
        note: str = Form(""),
        channel: str = Form("bot"),
        period: str = Form("30d"),
        back_to: str = Form("dashboard"),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        if back_to == "sources":
            back = f"/admin/sources?period={url_quote(period)}"
        else:
            back = f"/admin/?channel={url_quote(channel)}&period={url_quote(period)}"
        try:
            await credits_db.set_marketing_spend(month, int(spend_rub), source=source, note=note, actor=_user)
        except Exception as e:
            log.warning("marketing_spend_save_failed month=%s err=%r", month, e)
            return RedirectResponse(url=back + "&spend=" + url_quote(f"Не сохранено: {e}"), status_code=303)
        return RedirectResponse(url=back + "&spend=ok", status_code=303)

    @app.get("/admin/ops", response_class=HTMLResponse)
    async def ops_page(request: Request, _user: str = Depends(_check_auth)) -> str:
        ok_msg = html_mod.escape(str(request.query_params.get("ok", "")).strip())
        err_msg = html_mod.escape(str(request.query_params.get("err", "")).strip())
        metrics_data, windows_nodes_data, llm_workers_data, webhook_info, runtime_stats = await asyncio.gather(
            _safe_get_metrics(),
            _safe_get_windows_nodes(),
            _safe_get_llm_workers(),
            _safe_get_webhook_info(),
            _safe_get_runtime_stats(),
        )

        def _count_grid(data: object, *, empty: str = "нет данных") -> str:
            if not isinstance(data, dict) or not data:
                return f"<p>{empty}</p>"
            chips = ""
            for key, value in sorted(data.items(), key=lambda item: str(item[0])):
                chips += (
                    "<div class='stage-chip'>"
                    f"<div class='count'>{html_mod.escape(str(value))}</div>"
                    f"<div class='label'>{html_mod.escape(str(key))}</div>"
                    "</div>"
                )
            return f"<div class='stage-grid'>{chips}</div>"

        job_counts = metrics_data.get("job_status_counts") if isinstance(metrics_data, dict) else {}
        stage_counts = metrics_data.get("job_stage_counts") if isinstance(metrics_data, dict) else {}
        capacity_policy = metrics_data.get("capacity_policy") if isinstance(metrics_data, dict) else {}
        queue_topology = metrics_data.get("queue_topology") if isinstance(metrics_data, dict) else {}
        render_backlog = int(metrics_data.get("render_backlog", 0) or 0) if isinstance(metrics_data, dict) else 0
        build_backlog = int(metrics_data.get("build_backlog", 0) or 0) if isinstance(metrics_data, dict) else 0
        threshold = int((capacity_policy or {}).get("render_backlog_add_windows_node_threshold", 300) or 300)
        degraded_threshold = int((capacity_policy or {}).get("render_backlog_degraded_threshold", 100) or 100)
        build_degraded_threshold = int((capacity_policy or {}).get("build_backlog_degraded_threshold", 30) or 30)
        build_manual_threshold = int(
            (capacity_policy or {}).get("build_backlog_manual_maintenance_threshold", 80) or 80
        )
        policy_state = html_mod.escape(str((capacity_policy or {}).get("state") or "unknown"))
        policy_state_norm = str((capacity_policy or {}).get("state") or "").strip().lower()
        if policy_state_norm == "manual_maintenance_recommended":
            policy_badge_class = "badge-zero"
        elif policy_state_norm == "degraded":
            policy_badge_class = "badge-warn"
        else:
            policy_badge_class = "badge-ok"
        policy_reasons = []
        if isinstance(capacity_policy, dict):
            for reason in list(capacity_policy.get("reason_codes") or []):
                txt = str(reason or "").strip()
                if txt:
                    policy_reasons.append(txt)
        operator_action = html_mod.escape(str((capacity_policy or {}).get("operator_action") or "").strip())
        user_notice = html_mod.escape(str((capacity_policy or {}).get("user_message") or "").strip())
        render_split_active = bool((queue_topology or {}).get("render_poll_split_active")) if isinstance(queue_topology, dict) else False
        render_queue_default = html_mod.escape(
            str((queue_topology or {}).get("render_queue_default") or "—") if isinstance(queue_topology, dict) else "—"
        )
        render_poll_queue_default = html_mod.escape(
            str((queue_topology or {}).get("render_poll_queue_default") or "—")
            if isinstance(queue_topology, dict)
            else "—"
        )
        render_split_badge = "badge-ok" if render_split_active else "badge-zero"

        effective_nodes = windows_nodes_data.get("effective_urls") if isinstance(windows_nodes_data, dict) else []
        runtime_urls = windows_nodes_data.get("runtime_urls") if isinstance(windows_nodes_data, dict) else []
        llm_workers = llm_workers_data.get("workers") if isinstance(llm_workers_data, dict) else {}
        llm_rows = ""
        if isinstance(llm_workers, dict):
            for worker_name, row in sorted(llm_workers.items()):
                if not isinstance(row, dict):
                    continue
                enabled = "on" if bool(row.get("enabled")) else "off"
                llm_rows += (
                    "<tr>"
                    f"<td>{html_mod.escape(str(worker_name))}</td>"
                    f"<td>{enabled}</td>"
                    f"<td>{int(row.get('inflight', 0) or 0)}</td>"
                    f"<td>{int(row.get('max_inflight', 0) or 0)}</td>"
                    f"<td>{int(row.get('available_slots', 0) or 0)}</td>"
                    "</tr>"
                )

        webhook_url = html_mod.escape(str(webhook_info.get("url") or "").strip()) if isinstance(webhook_info, dict) else ""
        webhook_pending = int(webhook_info.get("pending_update_count", 0) or 0) if isinstance(webhook_info, dict) else 0
        webhook_error = html_mod.escape(str(webhook_info.get("last_error_message") or "").strip()) if isinstance(webhook_info, dict) else ""
        webhook_mode = html_mod.escape(str(getattr(settings, "tg_delivery_mode", "polling") or "polling"))

        runtime_error = html_mod.escape(str(runtime_stats.get("error") or "")) if isinstance(runtime_stats, dict) else ""
        run_status_counts = runtime_stats.get("run_status_counts") if isinstance(runtime_stats, dict) else {}
        outbox_status_counts = runtime_stats.get("outbox_status_counts") if isinstance(runtime_stats, dict) else {}
        outbox_oldest_due_age_s = runtime_stats.get("outbox_oldest_due_age_s") if isinstance(runtime_stats, dict) else {}
        old_runs_by_stage = runtime_stats.get("old_incomplete_runs_by_stage") if isinstance(runtime_stats, dict) else {}

        alert_configured = bool(str(getattr(settings, "alert_telegram_bot_token", "") or "").strip()) and bool(
            str(getattr(settings, "alert_telegram_chat_id", "") or "").strip()
        )
        friendly_preview = (
            "Мы не смогли корректно собрать видео. Деньги за эту попытку не списаны, "
            "а команда уже получила технические детали."
        )

        body = f"""
        {f'<div class="card"><p style="color:#4ecf8a"><strong>OK:</strong> {ok_msg}</p></div>' if ok_msg else ''}
        {f'<div class="card"><p style="color:#ef6a6a"><strong>Error:</strong> {err_msg}</p></div>' if err_msg else ''}

        <div class="card">
          <h2>Admin-only Smoke Checks</h2>
          <p>Эти проверки не создают пользовательскую job. Боевой smoke-job через Telegram ingress остается ручным release acceptance.</p>
          <form method="post" action="/admin/ops/alert-smoke">
            <button type="submit" class="btn-success">Send Alert Smoke</button>
          </form>
          <form method="post" action="/admin/ops/friendly-error-smoke" style="margin-left:8px">
            <button type="submit">Friendly Error Smoke</button>
          </form>
          <p style="margin-top:8px">Alert bot config: <span class="badge {'badge-ok' if alert_configured else 'badge-zero'}">{'ON' if alert_configured else 'OFF'}</span></p>
          <p>Friendly error preview: <em>{html_mod.escape(friendly_preview)}</em></p>
        </div>

        <div class="card">
          <h2>Webhook</h2>
          <div class="stage-grid">
            <div class="stage-chip"><div class="count">{webhook_mode}</div><div class="label">mode</div></div>
            <div class="stage-chip"><div class="count">{webhook_pending}</div><div class="label">pending</div></div>
          </div>
          {f'<p>URL: <code>{webhook_url}</code></p>' if webhook_url else '<p>URL: нет данных</p>'}
          {f"<p style='color:#ef6a6a'><strong>Last error:</strong> {webhook_error}</p>" if webhook_error else "<p style='color:#4ecf8a'>Last error: none</p>"}
        </div>

        <div class="card">
          <h2>Queue Snapshot</h2>
          <div class="stage-grid">
            <div class="stage-chip"><div class="count">{render_backlog}</div><div class="label">render backlog</div></div>
            <div class="stage-chip"><div class="count">{build_backlog}</div><div class="label">build backlog</div></div>
            <div class="stage-chip"><div class="count"><span class="badge {policy_badge_class}">{policy_state}</span></div><div class="label">policy state</div></div>
            <div class="stage-chip"><div class="count"><span class="badge {render_split_badge}">{'split' if render_split_active else 'shared'}</span></div><div class="label">render/poll queues</div></div>
          </div>
          <p>Backpressure thresholds: render degraded at <strong>{degraded_threshold}</strong>, add 3rd Windows node at <strong>{threshold}</strong>, build degraded at <strong>{build_degraded_threshold}</strong>, manual maintenance recommended at build backlog <strong>{build_manual_threshold}</strong>.</p>
          <p>Queue topology: dispatch queue <code>{render_queue_default}</code>, poll queue <code>{render_poll_queue_default}</code>.</p>
          {f'<p><strong>Policy reasons:</strong> {html_mod.escape(", ".join(policy_reasons))}</p>' if policy_reasons else '<p><strong>Policy reasons:</strong> none</p>'}
          {f'<p><strong>Operator action:</strong> {operator_action}</p>' if operator_action else '<p><strong>Operator action:</strong> none</p>'}
          {f'<p><strong>User copy:</strong> <em>{user_notice}</em></p>' if user_notice else '<p><strong>User copy:</strong> normal flow, no overload notice</p>'}
          <h3>Job statuses</h3>
          {_count_grid(job_counts)}
          <h3>Job stages</h3>
          {_count_grid(stage_counts)}
        </div>

        <div class="card">
          <h2>Windows / Render Pool</h2>
          <p>effective urls: {', '.join(html_mod.escape(str(x)) for x in (effective_nodes or [])) if effective_nodes else 'нет данных'}</p>
          <p>runtime urls: {', '.join(html_mod.escape(str(x)) for x in (runtime_urls or [])) if runtime_urls else 'нет данных'}</p>
          <p><a href="/admin/render-nodes">Render Nodes control &rarr;</a></p>
        </div>

        <div class="card">
          <h2>LLM Workers</h2>
          <div class="table-wrap">
            <table><tr><th>Worker</th><th>Enabled</th><th>Inflight</th><th>Max</th><th>Free</th></tr>
            {llm_rows or '<tr><td colspan="5">Нет данных</td></tr>'}
            </table>
          </div>
          <p><a href="/admin/llm-workers">LLM runtime config &rarr;</a></p>
        </div>

        <div class="card">
          <h2>Runtime / Outbox</h2>
          {f"<p style='color:#ef6a6a'><strong>Runtime error:</strong> {runtime_error}</p>" if runtime_error else ""}
          <h3>Run statuses</h3>
          {_count_grid(run_status_counts)}
          <h3>Outbox statuses</h3>
          {_count_grid(outbox_status_counts)}
          <h3>Oldest due outbox age, seconds</h3>
          {_count_grid(outbox_oldest_due_age_s)}
          <h3>Old incomplete runs by stage (&gt;15m)</h3>
          {_count_grid(old_runs_by_stage, empty="старых незавершенных run нет")}
          <p><a href="/admin/runs">Run-centric view &rarr;</a></p>
        </div>
        """
        return _page("Ops Toolkit", body)

    @app.post("/admin/ops/alert-smoke")
    async def ops_alert_smoke(_user: str = Depends(_check_auth)) -> RedirectResponse:
        try:
            await _send_alert_smoke(actor=_user)
            return RedirectResponse(
                f"/admin/ops?ok={quote_plus('alert smoke sent')}",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                f"/admin/ops?err={quote_plus(str(e))}",
                status_code=303,
            )

    @app.post("/admin/ops/friendly-error-smoke")
    async def ops_friendly_error_smoke(_user: str = Depends(_check_auth)) -> RedirectResponse:
        try:
            await _send_friendly_error_smoke(actor=_user)
            return RedirectResponse(
                f"/admin/ops?ok={quote_plus('friendly error smoke sent')}",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                f"/admin/ops?err={quote_plus(str(e))}",
                status_code=303,
            )

    # ── Season phase switcher (Hooks S1) ─────────────────────────────
    # Writes the active phase + meta to Redis under SEASON_REDIS_PREFIX.
    # tg_bot_botapi reads the same key on every menu render.

    from core.season_phase import PhaseStore, SeasonPhase  # type: ignore
    _season_store = PhaseStore(state_store.redis, prefix=settings.season_redis_prefix)

    _SEASON_PHASE_LABELS = [
        ("DEV_EARLY",      "DEV_EARLY — ранняя разработка (W1-W2)"),
        ("DEV_LATE",       "DEV_LATE — финал разработки (W3)"),
        ("PRE_LAUNCH",     "PRE_LAUNCH — подготовка к окну (W4)"),
        ("WINDOW_OPEN",    "WINDOW_OPEN — окно открыто (W5+)"),
        ("WINDOW_CLOSING", "WINDOW_CLOSING — последние 48ч"),
    ]

    @app.get("/admin/season", response_class=HTMLResponse)
    async def season_page(request: Request, _user: str = Depends(_check_auth)) -> str:
        snap = await _season_store.snapshot()
        ok_msg = html_mod.escape(str(request.query_params.get("ok", "")).strip())
        err_msg = html_mod.escape(str(request.query_params.get("err", "")).strip())

        next_window_iso = ""
        if snap.next_window_at > 0:
            next_window_iso = datetime.fromtimestamp(
                snap.next_window_at, tz=timezone.utc,
            ).strftime("%Y-%m-%dT%H:%M")

        phase_options = "".join(
            f'<option value="{val}"{" selected" if snap.phase.value == val else ""}>'
            f'{html_mod.escape(label)}</option>'
            for val, label in _SEASON_PHASE_LABELS
        )

        return f"""
<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><title>Season — Hooks S1</title>
<style>
 @font-face {{ font-family:'Point'; src:url('/admin/static/fonts/Point-Regular.ttf') format('truetype'); font-weight:400; font-display:swap; }}
 @font-face {{ font-family:'Point'; src:url('/admin/static/fonts/PointBold.ttf') format('truetype'); font-weight:700; font-display:swap; }}
 body{{font-family:'Point',system-ui,sans-serif;max-width:720px;margin:32px auto;padding:0 16px;background:#07030f;color:#f2effc}}
 h1{{margin-bottom:8px}}
 a{{color:#9b84ee}}
 form{{display:flex;flex-direction:column;gap:14px;background:#110b20;padding:18px;border-radius:12px;border:1px solid #221a3c}}
 label{{font-weight:600;font-size:14px}}
 input,select{{padding:8px 10px;font-size:15px;border:1px solid #322858;border-radius:8px;background:#1a1331;color:#f2effc;font-family:inherit}}
 button{{padding:10px 14px;background:linear-gradient(90deg,#9b84ee,#9b84ee);color:#fff;border:0;border-radius:8px;font-size:15px;cursor:pointer;align-self:flex-start;font-family:inherit}}
 button:hover{{opacity:.88}}
 .meta{{display:grid;grid-template-columns:160px 1fr;gap:6px 12px;margin:16px 0;font-size:14px}}
 .meta b{{color:rgba(246,245,253,.55)}}
 .flash{{padding:10px 14px;border-radius:8px;margin:12px 0;font-size:14px}}
 .flash-ok{{background:rgba(52,211,153,.14);border:1px solid rgba(52,211,153,.4);color:#34d399}}
 .flash-err{{background:rgba(251,113,133,.14);border:1px solid rgba(251,113,133,.4);color:#fb7185}}
 .nav a{{margin-right:14px;color:#9b84ee;text-decoration:none}}
</style>
</head><body>
<div class="nav">
  <a href="/admin/">← Дашборд</a>
  <a href="/admin/ops">Ops</a>
</div>
<h1>Season — Hooks S1</h1>
<p style="color:#c6bfe4;margin-top:0">Тумблер фазы сезона. Запись идёт в Redis под <code>{html_mod.escape(settings.season_redis_prefix)}</code>; tg_bot_botapi читает значение при каждом рендере меню.</p>

{f'<div class="flash flash-ok">{ok_msg}</div>' if ok_msg else ''}
{f'<div class="flash flash-err">{err_msg}</div>' if err_msg else ''}

<div class="meta">
  <b>Текущая фаза</b><span>{html_mod.escape(snap.phase.value)} ({html_mod.escape(snap.phase_label)})</span>
  <b>Сезон</b><span>№{snap.season_number} · {html_mod.escape(snap.season_theme)}</span>
  <b>Неделя</b><span>{snap.week} / 6</span>
  <b>До окна</b><span>{snap.days_until_window} дн.</span>
</div>

<form method="post" action="/admin/season">
  <label>Фаза
    <select name="phase">{phase_options}</select>
  </label>
  <label>Дата открытия окна (UTC)
    <input type="datetime-local" name="next_window_at" value="{next_window_iso}">
  </label>
  <label>Тема сезона
    <input type="text" name="season_theme" value="{html_mod.escape(snap.season_theme)}">
  </label>
  <label>Номер сезона
    <input type="number" name="season_number" value="{snap.season_number}" min="1">
  </label>
  <label>Неделя (1-6)
    <input type="number" name="week" value="{snap.week}" min="1" max="6">
  </label>
  <button type="submit">Применить</button>
</form>
</body></html>
"""

    @app.post("/admin/season")
    async def season_apply(
        phase: str = Form(...),
        next_window_at: str = Form(""),
        season_theme: str = Form(""),
        season_number: str = Form(""),
        week: str = Form(""),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        try:
            new_phase = SeasonPhase.parse(phase)
            await _season_store.set_phase(new_phase)

            window_ts: Optional[float] = None
            if next_window_at.strip():
                try:
                    dt = datetime.fromisoformat(next_window_at.strip())
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    window_ts = dt.timestamp()
                except ValueError:
                    pass

            await _season_store.set_meta(
                next_window_at=window_ts,
                season_theme=season_theme.strip() or None,
                season_number=int(season_number) if season_number.strip().isdigit() else None,
                week=int(week) if week.strip().isdigit() else None,
            )
            log.info("season_admin_applied phase=%s actor=%s", new_phase.value, _user)
            return RedirectResponse(
                f"/admin/season?ok={quote_plus('Фаза обновлена')}",
                status_code=303,
            )
        except Exception as e:
            log.exception("season_admin_failed: %s", e)
            return RedirectResponse(
                f"/admin/season?err={quote_plus(str(e))}",
                status_code=303,
            )

    # ── Render nodes / donor restart ────────────────────────────────

    @app.get("/admin/render-nodes", response_class=HTMLResponse)
    async def render_nodes_page(request: Request, _user: str = Depends(_check_auth)) -> str:
        ok_msg = html_mod.escape(str(request.query_params.get("ok", "")).strip())
        err_msg = html_mod.escape(str(request.query_params.get("err", "")).strip())

        donor_url = _cfg_str("windows_donor_url", "http://85.239.48.31:8000")
        donor_host = _cfg_str("windows_donor_host", "85.239.48.31")
        canary_audio = _cfg_str("windows_donor_canary_audio_s3_url")
        orchestrator_url = _cfg_str("orchestrator_public_url")
        restart_enabled = _donor_restart_enabled()

        pool_data: dict[str, object] = {}
        pool_err = ""
        try:
            pool_data = await _orchestrator_get_windows_nodes()
        except Exception as e:
            pool_err = html_mod.escape(str(e))

        runtime_urls = []
        effective_urls = []
        nodes = []
        if isinstance(pool_data, dict):
            runtime_urls = normalize_windows_urls(pool_data.get("runtime_urls") or [])
            effective_urls = normalize_windows_urls(pool_data.get("effective_urls") or [])
            nodes_obj = pool_data.get("nodes")
            nodes = nodes_obj if isinstance(nodes_obj, list) else []

        donor_probe = {"root": 0, "render": 0, "jobs": 0}
        donor_probe_err = ""
        try:
            donor_probe = await probe_render_node(donor_url, timeout_s=5.0)
        except Exception as e:
            donor_probe_err = html_mod.escape(str(e))

        twc_rows = ""
        twc_err = ""
        twc_token = _cfg_str("twc_token")
        twc_prefix = _cfg_str("twc_render_name_prefix", "blast-render-node")
        twc_source_id = max(0, _cfg_int("twc_render_source_server_id", 0))
        if twc_token:
            try:
                include_ids = {twc_source_id} if twc_source_id > 0 else set()
                servers = await list_render_servers(
                    token=twc_token,
                    name_prefix=twc_prefix,
                    include_ids=include_ids,
                )
                for srv in servers:
                    if not isinstance(srv, dict):
                        continue
                    sid = int(srv.get("id") or 0)
                    name = html_mod.escape(str(srv.get("name") or ""))
                    status = html_mod.escape(str(srv.get("status") or ""))
                    ipv4 = html_mod.escape(str(srv.get("ipv4") or ""))
                    twc_rows += (
                        f"<tr><td>{sid}</td><td>{name or '—'}</td><td>{status or '—'}</td>"
                        f"<td>{ipv4 or '—'}</td><td>{html_mod.escape(str(srv.get('updated_at') or ''))}</td></tr>"
                    )
            except Exception as e:
                twc_err = html_mod.escape(str(e))

        node_rows = ""
        for row in nodes:
            if not isinstance(row, dict):
                continue
            url = html_mod.escape(str(row.get("url") or ""))
            enabled = bool(row.get("enabled", True))
            reason = html_mod.escape(str(row.get("disabled_reason") or ""))
            disabled_at = row.get("disabled_at")
            node_rows += (
                f"<tr><td>{url or '—'}</td>"
                f"<td>{'on' if enabled else 'off'}</td>"
                f"<td>{reason or '—'}</td>"
                f"<td>{disabled_at if disabled_at is not None else '—'}</td></tr>"
            )

        restart_snap = await _donor_restart_snapshot()
        restart_running = bool(restart_snap.get("running", False))
        restart_status = html_mod.escape(str(restart_snap.get("status") or "idle"))
        restart_summary = html_mod.escape(str(restart_snap.get("summary") or ""))
        restart_error = html_mod.escape(str(restart_snap.get("last_error") or ""))
        restart_started = float(restart_snap.get("started_at") or 0.0)
        restart_finished = float(restart_snap.get("finished_at") or 0.0)
        restart_run_id = int(restart_snap.get("run_id") or 0)
        restart_actor = html_mod.escape(str(restart_snap.get("initiator") or ""))
        restart_cmd = html_mod.escape(str(restart_snap.get("command") or ""))
        log_tail = restart_snap.get("log_tail")
        log_lines = log_tail if isinstance(log_tail, list) else []
        restart_log_html = html_mod.escape("\n".join(str(x) for x in log_lines[-120:])) or "—"
        restart_btn_disabled = " disabled" if restart_running else ""
        restart_btn_label = "Restart in progress..." if restart_running else "Restart donor + canary"

        runtime_key = runtime_windows_urls_key(key_prefix=_cfg_str("jobstore_prefix", "blast"))
        runtime_urls_html = ", ".join(html_mod.escape(u) for u in runtime_urls) or "—"
        effective_urls_html = ", ".join(html_mod.escape(u) for u in effective_urls) or "—"
        canary_audio_present = "yes" if bool(canary_audio) else "no"

        body = f"""
        <div class="card">
        <h2>Donor restart control</h2>
        {f"<p style='color:#4ecf8a'><strong>OK:</strong> {ok_msg}</p>" if ok_msg else ""}
        {f"<p style='color:#ef6a6a'><strong>Ошибка:</strong> {err_msg}</p>" if err_msg else ""}
        <p><strong>Enabled:</strong> {'yes' if restart_enabled else 'no'}<br>
           <strong>Donor host:</strong> <code>{html_mod.escape(donor_host or '—')}</code><br>
           <strong>Donor URL:</strong> <code>{html_mod.escape(donor_url or '—')}</code><br>
           <strong>Orchestrator URL:</strong> <code>{html_mod.escape(orchestrator_url or '—')}</code><br>
           <strong>Canary audio configured:</strong> {canary_audio_present}</p>
        <form method="post" action="/admin/render-nodes/restart-donor"
              onsubmit="return confirm('Restart donor and run canary?');">
          <button type="submit" class="btn-danger"{restart_btn_disabled}>{restart_btn_label}</button>
        </form>
        <p style="margin-top:8px;color:#c6bfe4;font-size:0.88em">
          Запуск идет в фоне через <code>scripts/windows_node_rollout.py</code>.
          Повторный старт блокируется, пока текущий run не завершится.
        </p>
        </div>

        <div class="card">
        <h3>Restart run status</h3>
        <p><strong>run_id:</strong> {restart_run_id} &nbsp;|&nbsp;
           <strong>status:</strong> {restart_status} &nbsp;|&nbsp;
           <strong>initiator:</strong> {restart_actor or '—'}</p>
        <p><strong>started_at:</strong> {restart_started or '—'} &nbsp;|&nbsp;
           <strong>finished_at:</strong> {restart_finished or '—'}</p>
        <p><strong>summary:</strong> {restart_summary or '—'}</p>
        {f"<p style='color:#ef6a6a'><strong>error:</strong> {restart_error}</p>" if restart_error else ""}
        <p><strong>command:</strong> <code>{restart_cmd or '—'}</code></p>
        <pre style="white-space:pre-wrap;max-height:360px;overflow:auto;background:#1a1331;padding:12px;border-radius:6px;font-size:0.82em">{restart_log_html}</pre>
        </div>

        <div class="card">
        <h3>Runtime pool</h3>
        {f"<p style='color:#ef6a6a'><strong>Ошибка:</strong> {pool_err}</p>" if pool_err else ""}
        <p><strong>runtime key:</strong> <code>{html_mod.escape(runtime_key)}</code><br>
           <strong>runtime urls:</strong> {runtime_urls_html}<br>
           <strong>effective urls:</strong> {effective_urls_html}</p>
        <div class="table-wrap">
        <table><tr><th>URL</th><th>Enabled</th><th>Disabled reason</th><th>Disabled at</th></tr>
        {node_rows if node_rows else '<tr><td colspan="4">Нет данных</td></tr>'}</table>
        </div>
        </div>

        <div class="card">
        <h3>Donor probe</h3>
        {f"<p style='color:#ef6a6a'><strong>Ошибка:</strong> {donor_probe_err}</p>" if donor_probe_err else ""}
        <p><strong>root:</strong> {int(donor_probe.get('root', 0) or 0)} &nbsp;|&nbsp;
           <strong>render:</strong> {int(donor_probe.get('render', 0) or 0)} &nbsp;|&nbsp;
           <strong>jobs:</strong> {int(donor_probe.get('jobs', 0) or 0)}</p>
        </div>

        <div class="card">
        <h3>Timeweb Windows servers</h3>
        {f"<p style='color:#ef6a6a'><strong>Ошибка:</strong> {twc_err}</p>" if twc_err else ""}
        <div class="table-wrap">
        <table><tr><th>ID</th><th>Name</th><th>Status</th><th>IPv4</th><th>Updated</th></tr>
        {twc_rows if twc_rows else '<tr><td colspan="5">Нет данных (или TWC_TOKEN не задан)</td></tr>'}</table>
        </div>
        </div>
        """
        return _page("Render Nodes", body)

    @app.post("/admin/render-nodes/restart-donor")
    async def restart_donor(_user: str = Depends(_check_auth)) -> RedirectResponse:
        if not _donor_restart_enabled():
            return RedirectResponse(
                f"/admin/render-nodes?err={quote_plus('ADMIN_PANEL_ENABLE_DONOR_RESTART is disabled')}",
                status_code=303,
            )

        async with donor_restart_lock:
            if bool(donor_restart_state.get("running", False)):
                run_id = int(donor_restart_state.get("run_id") or 0)
                return RedirectResponse(
                    f"/admin/render-nodes?err={quote_plus(f'restart already running run_id={run_id}')}",
                    status_code=303,
                )
            run_id = int(donor_restart_state.get("run_id") or 0) + 1
            donor_restart_state["run_id"] = run_id

        try:
            cmd = _build_donor_restart_command()
        except Exception as e:
            return RedirectResponse(
                f"/admin/render-nodes?err={quote_plus(str(e))}",
                status_code=303,
            )

        asyncio.create_task(_run_donor_restart_background(run_id=run_id, actor=_user, cmd=cmd))
        return RedirectResponse(
            f"/admin/render-nodes?ok={quote_plus(f'restart started run_id={run_id}')}",
            status_code=303,
        )

    # ── Users list ────────────────────────────────────────────────────

    @app.get("/admin/users", response_class=HTMLResponse)
    async def users_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        q = request.query_params.get("q", "").strip()
        page = _query_int(request, "page", default=1, min_value=1)
        per_page = 50
        offset = (page - 1) * per_page

        if q:
            users = await credits_db.search_users(q, limit=per_page)
            total = len(users)
            total_pages = 1
        else:
            users = await credits_db.list_users(limit=per_page, offset=offset)
            total = await credits_db.count_users()
            total_pages = max(1, (total + per_page - 1) // per_page)

        # Get current stages for requested page only (no full state scan).
        stages_map = await state_store.get_stages_for_chat_ids([int(u["tg_id"]) for u in users])

        rows = ""
        for u in users:
            badge = "badge-ok" if u["credits"] > 0 else "badge-zero"
            uname = f"@{u['username']}" if u["username"] else str(u["tg_id"])
            stage = stages_map.get(u["tg_id"], "—")
            stage_lbl = _stage_label(stage) if stage != "—" else "—"
            src = u.get("source", "")
            src_cell = f'<a href="/admin/sources/{url_quote(src, safe="")}" class="badge badge-source">{html_mod.escape(src)}</a>' if src else '<span style="color:#4a4470">—</span>'
            rows += (
                f"<tr><td><a href='/admin/users/{u['tg_id']}'>{uname}</a></td>"
                f"<td>{u['tg_id']}</td>"
                f"<td><span class='badge {badge}'>{u['credits']}</span></td>"
                f"<td><span class='badge badge-stage'>{stage_lbl}</span></td>"
                f"<td>{src_cell}</td>"
                f"<td>{u['created_at']}</td>"
                f"<td>{u['updated_at']}</td></tr>"
            )

        q_escaped = html_mod.escape(q)
        search_note = f'<p>Результаты поиска: <strong>{q_escaped}</strong> ({total})</p>' if q else ""
        base_url = f"?q={q_escaped}&" if q else "?"

        body = f"""
        <div class="card">
        {search_note}
        <p>Total: {total}</p>
        <div class="table-wrap">
        <table><tr><th>Username</th><th>tg_id</th><th>Credits</th><th>Этап</th><th>Источник</th><th>Created</th><th>Updated</th></tr>
        {rows}</table>
        </div>
        {_pagination_html(page, total_pages, base_url)}
        </div>
        """
        return _page("Users", body)

    # ── User detail ───────────────────────────────────────────────────

    @app.get("/admin/users/{tg_id}", response_class=HTMLResponse)
    async def user_detail(tg_id: int, _user: str = Depends(_check_auth)) -> str:
        user = await credits_db.get_user(tg_id)
        if not user:
            raise HTTPException(404, "User not found")
        uname = f"@{user['username']}" if user["username"] else str(tg_id)

        # Current stage from Redis
        st = await state_store.get(tg_id)
        stage_lbl = _stage_label(st.stage) if st else "—"

        # Source
        source = await credits_db.get_user_source(tg_id)
        source_badge = f'<span class="badge badge-source">{html_mod.escape(source)}</span>' if source else '<span style="color:#8d86ad">direct</span>'

        # Package options
        pkg_options = "".join(f'<option value="{v}">{lbl}</option>' for v, lbl in _PACKAGES.items())

        # Health + CRM data
        metrics = await credits_db.user_health_metrics(tg_id)
        health_label, health_class = _health_label(metrics, int(user["credits"]))
        tags = await credits_db.get_user_tags(tg_id)
        notes = await credits_db.get_user_notes(tg_id)
        manual_payments = await credits_db.list_manual_payments(tg_id, limit=50)
        purchases = await credits_db.get_user_purchases(tg_id)
        user_tier = await credits_db.get_user_tier(tg_id)
        tier_spec = _TIER_SPEC.get(user_tier) if user_tier else None
        tier_badge = (
            f'<a href="/admin/tiers?tier={user_tier}" class="badge" '
            f'style="background:color-mix(in srgb, {tier_spec["color"]} 18%, transparent);color:{tier_spec["color"]};text-decoration:none">'
            f'{user_tier} — {html_mod.escape(tier_spec["title"])}</a>'
            if tier_spec else
            ('<span class="badge badge-stage">' + html_mod.escape(user_tier) + '</span>' if user_tier
             else '<span class="badge badge-zero">не классифицирован</span>')
        )
        tag_badges = " ".join(
            f'<span class="badge badge-source">{html_mod.escape(t)}'
            f' <a href="#" onclick="document.getElementById(\'rmtag-{html_mod.escape(t, quote=True)}\').submit();return false" '
            f'style="color:#ef6a6a;margin-left:4px;text-decoration:none">&times;</a></span>'
            f'<form id="rmtag-{html_mod.escape(t, quote=True)}" method="post" '
            f'action="/admin/users/{tg_id}/tags/remove" style="display:none">'
            f'<input type="hidden" name="tag" value="{html_mod.escape(t, quote=True)}"></form>'
            for t in tags
        )
        notes_html = "".join(
            f'<div style="border-left:3px solid #5aa9ea;padding:6px 10px;margin:0.5rem 0;background:#1a1331">'
            f'<div>{html_mod.escape(n["note"])}</div>'
            f'<small style="color:#c6bfe4">{n["created_at"]} · {html_mod.escape(n["created_by"] or "—")} '
            f'· <form method="post" action="/admin/users/{tg_id}/notes/{n["id"]}/delete" style="display:inline" '
            f'onsubmit="return confirm(\'Удалить заметку?\')">'
            f'<button style="background:none;color:#ef6a6a;padding:0;font-size:0.8em;cursor:pointer;border:none">удалить</button>'
            f'</form></small></div>'
            for n in notes
        )

        txs = await credits_db.get_transactions(tg_id, limit=50)
        tx_rows = ""
        for t in txs:
            sign = "+" if t["amount"] > 0 else ""
            tx_rows += (
                f"<tr><td>{t['id']}</td><td>{sign}{t['amount']}</td>"
                f"<td>{t['reason']}</td><td>{html_mod.escape(str(t.get('actor') or '—'))}</td>"
                f"<td>{html_mod.escape(str(t.get('order_id') or '—'))}</td>"
                f"<td>{t['admin_note']}</td><td>{t['created_at']}</td></tr>"
            )

        # Payment history (bot orders only — manual payments are rendered separately).
        payments_hist = await credits_db.user_payments_history(tg_id, limit=50)
        pay_rows = ""
        for p in payments_hist:
            st = str(p["status"] or "").upper()
            color = {
                "CONFIRMED": "#4ecf8a", "AUTHORIZED": "#5aa9ea",
                "CHARGE_FAILED": "#ef6a6a", "REJECTED": "#ef6a6a",
                "REFUNDED": "#8d86ad", "CANCELED": "#8d86ad",
            }.get(st, "#8d86ad")
            kind = "↻ авто" if p["is_recurrent"] else "разовый"
            pay_rows += (
                f"<tr><td>{p['created_at']}</td>"
                f"<td>{p['amount_rub']}₽</td>"
                f"<td>{html_mod.escape(p['package'])}</td>"
                f"<td>{kind}</td>"
                f"<td><span class='badge' style='background:{color};color:white'>{html_mod.escape(st)}</span></td>"
                f"<td><code style='font-size:0.8em'>{html_mod.escape(p['order_id'])}</code></td></tr>"
            )

        # Active subscription card data
        active_sub = await credits_db.get_active_subscription(tg_id)
        sub_card_html = ""
        if active_sub:
            nc = active_sub.get("next_charge_at")
            nc_str = nc.strftime("%Y-%m-%d %H:%M") if hasattr(nc, "strftime") else "—"
            rb = active_sub.get("rebill_id", "")
            rb_masked = (rb[:4] + "…" + rb[-4:]) if len(rb) > 8 else rb
            sub_card_html = f"""
            <div class="card">
              <h3>Активная подписка</h3>
              <p>Пакет: <b>{html_mod.escape(active_sub['package'])}</b> ·
                 Сумма: <b>{active_sub['amount_rub']}₽</b> ·
                 След. списание: <b style="color:#4ecf8a">{nc_str}</b> ·
                 Retries: {active_sub.get('charge_retries', 0)} ·
                 RebillId: <code>{html_mod.escape(rb_masked)}</code></p>
              <form method="post" action="/admin/subscriptions/{active_sub['id']}/charge" style="display:inline"
                    onsubmit="return confirm('Запустить ручное списание ПРЯМО СЕЙЧАС? Карта будет реально списана.')">
                <button type="submit" class="btn-success">Списать сейчас (тест)</button>
              </form>
              <a href="/admin/subscriptions" class="btn" style="background:#1a1331;color:#8d86ad">Все подписки →</a>
            </div>
            """

        # Activity log
        acts = await credits_db.get_activity(tg_id, limit=50)
        act_rows = ""
        for a in acts:
            act_rows += (
                f"<tr><td>{a['id']}</td><td>{_event_label(a['event'])}</td>"
                f"<td>{a['detail']}</td><td>{a['created_at']}</td></tr>"
            )

        # Lifecycle fires for the last 7 days
        lifecycle_fires = await credits_db.recent_lifecycle_fires_for_user(tg_id, days=7)
        lifecycle_rows = ""
        for f in lifecycle_fires:
            tier_html = ""
            if f.get("rule_tier"):
                tspec = _TIER_SPEC.get(f["rule_tier"])
                bg = (tspec or {}).get("color", "#8d86ad")
                tier_html = f'<span class="badge" style="background:{bg};color:white">{f["rule_tier"]}</span>'
            else:
                tier_html = '<span style="color:#8d86ad">—</span>'
            status_badge = {
                "sent": '<span class="badge badge-ok">отпр</span>',
                "blocked": '<span class="badge badge-zero">блок</span>',
                "failed": '<span class="badge badge-zero">ошиб</span>',
                "throttled": '<span class="badge badge-warn">throttled</span>',
                "test": '<span class="badge badge-stage">test</span>',
            }.get(f.get("status", ""), html_mod.escape(f.get("status", "")))
            lifecycle_rows += (
                f"<tr><td>{html_mod.escape(f.get('created_at') or '')}</td>"
                f"<td>{tier_html}</td>"
                f"<td>#{f['rule_id']} <a href='/admin/lifecycle/{f['rule_id']}'>{html_mod.escape(f.get('rule_name') or '—')}</a></td>"
                f"<td>{status_badge}</td>"
                f"<td><small>{html_mod.escape((f.get('error') or '')[:160])}</small></td>"
                f"</tr>"
            )

        initials = (user["username"] or str(tg_id))[:2].upper()
        pay_status_ru = {"CONFIRMED": "оплачен", "AUTHORIZED": "авторизован", "CHARGE_FAILED": "не прошёл", "REJECTED": "отклонён", "REFUNDED": "возврат", "CANCELED": "отменён", "NEW": "создан"}
        pay_rows = ""
        for pmt in payments_hist:
            st_key = str(pmt["status"] or "").upper()
            cls = {"CONFIRMED": "badge-ok", "AUTHORIZED": "badge-source", "CHARGE_FAILED": "badge-zero", "REJECTED": "badge-zero"}.get(st_key, "badge-stage")
            pay_rows += (
                f"<tr><td>{pmt['created_at']}</td><td><b>{pmt['amount_rub']} ₽</b></td><td>{html_mod.escape(_PACKAGES.get(str(pmt['package']), str(pmt['package'])))}</td>"
                f"<td>{'автосписание' if pmt['is_recurrent'] else 'разовый'}</td>"
                f"<td><span class='badge {cls}'>{pay_status_ru.get(st_key, st_key.lower())}</span></td>"
                f"<td><code style='font-size:0.8em'>{html_mod.escape(pmt['order_id'])}</code></td></tr>"
            )
        notes_html = "".join(
            f'<div class="note"><div>{html_mod.escape(n["note"])}</div>'
            f'<small>{n["created_at"]} · {html_mod.escape(n["created_by"] or "—")} · '
            f'<form method="post" action="/admin/users/{tg_id}/notes/{n["id"]}/delete" style="display:inline" onsubmit="return confirm(\'Удалить заметку?\')">'
            f'<button class="linkish">удалить</button></form></small></div>'
            for n in notes
        )
        sub_line = ""
        if active_sub:
            nc = active_sub.get("next_charge_at")
            nc_str = nc.strftime("%d.%m.%Y") if hasattr(nc, "strftime") else "—"
            sub_line = f"""
              <div class="row" style="justify-content:space-between;background:var(--ok-bg);border-radius:12px;padding:10px 14px">
                <div><b style="color:var(--ok)">Подписка активна</b> · {html_mod.escape(_PACKAGES.get(str(active_sub['package']), str(active_sub['package'])))} · {active_sub['amount_rub']} ₽/мес · следующее списание {nc_str}</div>
                <form method="post" action="/admin/subscriptions/{active_sub['id']}/charge" style="display:inline" onsubmit="return confirm('Списать прямо сейчас? Карта будет реально списана.')"><button type="submit" class="btn-secondary btn-sm">Списать сейчас</button></form>
              </div>"""
        body = f"""
        <div class="card">
          <div class="u-head">
            <div class="row" style="gap:16px;align-items:flex-start">
              <div class="avatar">{html_mod.escape(initials)}</div>
              <div>
                <h1>{html_mod.escape(uname)}</h1>
                <div class="meta">id {tg_id} · с нами с {user['created_at']} · последняя активность {user['updated_at']}</div>
                <div class="badges">
                  <span class="badge {health_class}">{health_label}</span>
                  {tier_badge}
                  <span class="badge badge-stage">{stage_lbl}</span>
                  {source_badge}
                </div>
              </div>
            </div>
            <div class="pm-kpis" style="grid-template-columns: repeat(4, minmax(120px, auto)); gap: 12px 26px">
              <div class="pm-kpi"><div class="l">Баланс</div><div class="v acc" style="font-size:1.7em">{user['credits']}</div><div class="s">кредитов</div></div>
              <div class="pm-kpi"><div class="l">Роликов</div><div class="v" style="font-size:1.7em">{metrics['gens_done']}</div><div class="s">за 30 дн — {metrics['gens_done_30d']}</div></div>
              <div class="pm-kpi"><div class="l">Выручка</div><div class="v" style="font-size:1.7em">{f"{metrics['revenue_rub']:,}".replace(",", " ")} ₽</div><div class="s">бот {f"{metrics.get('revenue_bot', 0):,}".replace(",", " ")} · вручную {f"{metrics.get('revenue_manual', 0):,}".replace(",", " ")}</div></div>
              <div class="pm-kpi"><div class="l">Оплат</div><div class="v" style="font-size:1.7em">{metrics['paid_orders']}</div><div class="s">последний ролик {metrics['last_gen_at'] or '—'}</div></div>
            </div>
          </div>
        </div>

        <div class="pm-grid">
          <div style="grid-column: span 8" class="stack" >
            <div class="pm-panel">
              <div class="ph"><b>Покупки</b></div>
              {sub_line}
              {_purchases_html({**purchases, 'active_subscription': None} if active_sub else purchases)}
            </div>
            <div class="pm-panel">
              <div class="ph"><b>Платежи через бота</b></div>
              <div class="table-wrap"><table><tr><th>Дата</th><th>Сумма</th><th>Пакет</th><th>Тип</th><th>Статус</th><th>Заказ</th></tr>
              {pay_rows if pay_rows else '<tr><td colspan="6">Платежей через бота пока нет</td></tr>'}</table></div>
            </div>
            <div class="pm-panel">
              <div class="ph"><b>Ручная выручка</b><span>наличные, инвойсы — учитываются в выручке клиента</span></div>
              {_manual_payments_html(tg_id, manual_payments)}
              <form method="post" action="/admin/users/{tg_id}/manual-payment/add" class="row">
                <input type="number" name="amount_rub" placeholder="сумма, ₽" required style="width:130px" min="-1000000" max="10000000">
                <input type="text" name="note" placeholder="комментарий" style="flex:1;min-width:200px">
                <button type="submit" class="btn-secondary">Добавить</button>
              </form>
            </div>
            <div class="pm-panel">
              <div class="ph"><b>Действия</b><span>последние 50</span></div>
              <div class="table-wrap"><table><tr><th>Событие</th><th>Детали</th><th>Когда</th></tr>
              {''.join(f"<tr><td>{_event_label(a_['event'])}</td><td class='meta'>{html_mod.escape(str(a_['detail'] or ''))[:120]}</td><td>{a_['created_at']}</td></tr>" for a_ in acts) if acts else '<tr><td colspan="3">Нет данных</td></tr>'}</table></div>
            </div>
            <div class="pm-panel">
              <div class="ph"><b>Автосообщения за 7 дней</b><span>что из триггеров дошло до клиента</span></div>
              <div class="table-wrap"><table><tr><th>Когда</th><th>Тир</th><th>Правило</th><th>Статус</th><th>Ошибка</th></tr>
              {lifecycle_rows if lifecycle_rows else '<tr><td colspan="5">За неделю ничего не отправлялось</td></tr>'}</table></div>
            </div>
            <div class="pm-panel">
              <div class="ph"><b>Движение кредитов</b></div>
              <div class="table-wrap"><table><tr><th>#</th><th>Сколько</th><th>Причина</th><th>Кто</th><th>Заказ</th><th>Заметка</th><th>Когда</th></tr>
              {tx_rows if tx_rows else '<tr><td colspan="7">Нет данных</td></tr>'}</table></div>
            </div>
          </div>

          <div style="grid-column: span 4" class="stack">
            <div class="pm-panel" style="gap:12px">
              <div class="ph"><b>Написать от бота</b></div>
              <form method="post" action="/admin/users/{tg_id}/message" class="stack" style="gap:10px">
                <textarea name="text" required placeholder="Текст сообщения (HTML)…" style="min-height:90px;width:100%"></textarea>
                <div class="row" style="justify-content:space-between">
                  <select name="parse_mode" style="width:150px"><option value="HTML" selected>HTML</option><option value="MARKDOWN">Markdown</option><option value="">без разметки</option></select>
                  <button type="submit" onclick="return confirm('Отправить сообщение от имени бота?')">Отправить</button>
                </div>
              </form>
            </div>
            <div class="pm-panel" style="gap:10px">
              <div class="ph"><b>Заметки</b></div>
              <div class="stack" style="gap:8px">{notes_html or '<p class="meta" style="margin:0">Пока нет заметок</p>'}</div>
              <form method="post" action="/admin/users/{tg_id}/notes/add" class="stack" style="gap:8px">
                <textarea name="note" required placeholder="Контекст, договорённости…" style="min-height:60px;width:100%"></textarea>
                <button type="submit" class="btn-secondary" style="align-self:flex-start">Добавить заметку</button>
              </form>
            </div>
            <div class="pm-panel" style="gap:10px">
              <div class="ph"><b>Теги</b></div>
              <div class="row" style="gap:6px">{tag_badges or '<span class="meta">нет</span>'}</div>
              <form method="post" action="/admin/users/{tg_id}/tags/add" class="row">
                <input type="text" name="tag" placeholder="vip, artist, agency…" style="flex:1;min-width:120px" required>
                <button type="submit" class="btn-secondary btn-sm">Добавить</button>
              </form>
            </div>
            <div class="pm-panel" style="gap:10px">
              <div class="ph"><b>Начислить кредиты</b></div>
              <form method="post" action="/admin/users/{tg_id}/credits" class="stack" style="gap:8px">
                <div class="row"><input type="number" name="amount" value="0" min="-1000" max="10000" style="width:110px"><input type="text" name="reason" placeholder="причина" style="flex:1;min-width:120px"></div>
                <div class="row"><input type="text" name="order_id" placeholder="заказ (необязательно)" style="flex:1;min-width:120px"><input type="text" name="note" placeholder="заметка" style="flex:1;min-width:120px"></div>
                <button type="submit" class="btn-secondary" style="align-self:flex-start">Начислить</button>
              </form>
            </div>
            <div class="pm-panel" style="gap:10px">
              <div class="ph"><b>Активировать пакет</b><span>оплата вне бота</span></div>
              <p class="meta" style="margin:0">Начислит кредиты, переведёт клиента к загрузке аудио и пришлёт ему уведомление.</p>
              <form method="post" action="/admin/users/{tg_id}/activate" class="row" onsubmit="return confirm('Активировать пакет для {html_mod.escape(uname, quote=True).replace(chr(39), "&#39;")}?')">
                <select name="package" style="flex:1">{pkg_options}</select>
                <button type="submit">Активировать</button>
              </form>
            </div>
          </div>
        </div>
        """
        return _page("", body, back=("Клиенты", "/admin/clients"))

    @app.post("/admin/users/{tg_id}/credits")
    async def user_add_credits(
        tg_id: int,
        amount: int = Form(...),
        reason: str = Form("admin_panel"),
        order_id: str = Form(""),
        note: str = Form(""),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        note_parts = [str(note or "").strip(), f"via panel by {_user}"]
        merged_note = " | ".join(part for part in note_parts if part)
        await credits_db.add_credits(
            tg_id,
            amount,
            reason,
            admin_note=merged_note,
            actor=_user,
            order_id=str(order_id or "").strip(),
        )
        return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)

    # ── Activate package (external payment) ───────────────────────────

    @app.post("/admin/users/{tg_id}/activate")
    async def user_activate_package(tg_id: int, package: int = Form(...), _user: str = Depends(_check_auth)) -> RedirectResponse:
        pkg_label = _PACKAGES.get(str(package), f"{package} credits")
        # 1. Add credits
        await credits_db.add_credits(
            tg_id, package,
            reason="admin_activate",
            admin_note=f"{pkg_label} — activated by {_user}",
        )
        # 1b. Add the tariff's unique-track allowance (no runtime payment row
        # exists for a manual activation, so grant the base directly).
        tracks_to_add = _PACKAGE_TRACKS.get(str(package), 0)
        if tracks_to_add:
            await credits_db.add_track_credits(
                tg_id, tracks_to_add, "admin_activate",
                admin_note=f"{pkg_label} — activated by {_user}",
            )
        # 2. Move to WAIT_AUDIO
        await state_store.reset_to_wait_audio(tg_id)
        # 3. Log event
        await credits_db.log_event(tg_id, "admin_activate", f"{pkg_label} by {_user}")
        # 4. Notify user via Telegram
        if bot_ref and bot_ref[0]:
            try:
                bal = await credits_db.get_balance(tg_id)
                track_bal = await credits_db.get_track_balance(tg_id)
                tracks_line = f"Доступно уникальных треков: {track_bal}\n" if track_bal > 0 else ""
                await bot_ref[0].send_message(
                    tg_id,
                    f"Пакет активирован!\n"
                    f"Начислено {package} генераций. Доступно: {bal}\n"
                    f"{tracks_line}\n"
                    "Отправь трек аудио-файлом, и я соберу клип.",
                )
            except Exception as e:
                log.warning("activate: failed to notify user %s: %s", tg_id, e)
        return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)

    # ── Activity log ──────────────────────────────────────────────────

    @app.get("/admin/activity", response_class=HTMLResponse)
    async def activity_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        page = _query_int(request, "page", default=1, min_value=1)
        per_page = 50
        offset = (page - 1) * per_page
        acts, total = await asyncio.gather(
            credits_db.get_activity(limit=per_page, offset=offset),
            credits_db.count_activity(),
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
        rows = ""
        for a in acts:
            rows += (
                f"<tr><td>{a['id']}</td>"
                f"<td><a href='/admin/users/{a['tg_id']}'>{a['tg_id']}</a></td>"
                f"<td>{_event_label(a['event'])}</td>"
                f"<td>{html_mod.escape(str(a['detail']))}</td>"
                f"<td>{a['created_at']}</td></tr>"
            )
        body = f"""
        <div class="card">
        <p>Total: {total}</p>
        <div class="table-wrap">
        <table><tr><th>#</th><th>tg_id</th><th>Событие</th><th>Детали</th><th>Дата</th></tr>
        {rows}</table>
        </div>
        {_pagination_html(page, total_pages)}
        </div>
        """
        return _page("Activity Log", body)

    # ── Transactions ──────────────────────────────────────────────────

    @app.get("/admin/transactions", response_class=HTMLResponse)
    async def transactions_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        page = _query_int(request, "page", default=1, min_value=1)
        per_page = 50
        offset = (page - 1) * per_page
        txs, total = await asyncio.gather(
            credits_db.get_transactions(limit=per_page, offset=offset),
            credits_db.count_transactions(),
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
        rows = ""
        for t in txs:
            sign = "+" if t["amount"] > 0 else ""
            rows += (
                f"<tr><td>{t['id']}</td><td>{t['tg_id']}</td><td>{sign}{t['amount']}</td>"
                f"<td>{t['reason']}</td>"
                f"<td>{html_mod.escape(str(t.get('actor') or '—'))}</td>"
                f"<td>{html_mod.escape(str(t.get('order_id') or '—'))}</td>"
                f"<td>{html_mod.escape(str(t['admin_note']))}</td><td>{t['created_at']}</td></tr>"
            )
        body = f"""
        <div class="card">
        <p>Total: {total}</p>
        <div class="table-wrap">
        <table><tr><th>#</th><th>tg_id</th><th>Amount</th><th>Reason</th><th>Actor</th><th>Order</th><th>Note</th><th>Date</th></tr>
        {rows}</table>
        </div>
        {_pagination_html(page, total_pages)}
        </div>
        """
        return _page("Transactions", body)

    # ── Payments list ────────────────────────────────────────────────

    @app.get("/admin/payments", response_class=HTMLResponse)
    async def payments_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        page = _query_int(request, "page", default=1, min_value=1)
        per_page = 50
        offset = (page - 1) * per_page
        pays, total = await asyncio.gather(
            credits_db.get_payments(limit=per_page, offset=offset),
            credits_db.count_payments(),
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
        rows = ""
        for p in pays:
            status_cls = "badge-ok" if p["status"] == "CONFIRMED" else "badge-zero"
            rows += (
                f"<tr><td>{p['id']}</td>"
                f"<td><a href='/admin/users/{p['tg_id']}'>{p['tg_id']}</a></td>"
                f"<td>{p['order_id']}</td>"
                f"<td>{p['amount_rub']}&rub;</td>"
                f"<td>{p['package']}</td>"
                f"<td><span class='badge {status_cls}'>{p['status']}</span></td>"
                f"<td>{p['created_at']}</td></tr>"
            )
        body = f"""
        <div class="card">
        <p>Total: {total}</p>
        <div class="table-wrap">
        <table><tr><th>#</th><th>tg_id</th><th>Order</th><th>Amount</th><th>Package</th><th>Status</th><th>Date</th></tr>
        {rows}</table>
        </div>
        {_pagination_html(page, total_pages)}
        </div>
        """
        return _page("Payments", body)

    # ── UTM summary ─────────────────────────────────────────────────

    # ── Sources (start-param tracking) ────────────────────────────────

    @app.get("/admin/sources", response_class=HTMLResponse)
    async def sources_page(request: Request, _user: str = Depends(_check_auth)) -> str:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td

        now_utc = _dt.now(_tz.utc)
        presets = {"7d": ("7 дней", 7), "30d": ("30 дней", 30), "90d": ("90 дней", 90), "365d": ("Год", 365)}
        period = str(request.query_params.get("period", "30d")).strip()
        if period not in presets:
            period = "30d"
        period_from = now_utc - _td(days=presets[period][1])
        econ, spend_rows = await asyncio.gather(
            credits_db.source_economics(period_from, now_utc),
            credits_db.list_marketing_spend(limit=60),
        )
        spend_param = str(request.query_params.get("spend", "")).strip()
        flash = (
            '<div class="flash flash-ok">Расходы сохранены</div>' if spend_param == "ok"
            else f'<div class="flash flash-err">{html_mod.escape(spend_param)}</div>' if spend_param else ""
        )
        pills = '<div class="seg">' + "".join(
            f'<a href="/admin/sources?period={key}" class="{"on" if period == key else ""}">{label}</a>'
            for key, (label, _) in presets.items()
        ) + "</div>"

        def _r(v):
            return "—" if v is None else f"{int(round(v)):,}".replace(",", " ") + " ₽"

        totals = {k: sum(int(r[k]) for r in econ) for k in ("users_total", "users_new", "payers_total", "payers_new", "revenue_period", "spend_rub")}
        rows = ""
        for r in econ:
            src_url = url_quote(r["source"], safe="")
            roas_txt = "—" if r["roas"] is None else "%.1f×" % r["roas"]
            rows += (
                f"<tr><td><a href='/admin/sources/{src_url}'>{html_mod.escape(r['source'])}</a></td>"
                f"<td>{r['users_new']} <span class='meta'>/ {r['users_total']}</span></td>"
                f"<td>{r['payers_new']} <span class='meta'>/ {r['payers_total']}</span></td>"
                f"<td>{_r(r['revenue_period'])}</td><td>{_r(r['spend_rub']) if r['spend_rub'] else '—'}</td>"
                f"<td>{_r(r['cost_per_user'])}</td><td><strong>{_r(r['cac'])}</strong></td>"
                f"<td>{roas_txt}</td></tr>"
            )
        total_cac = (totals["spend_rub"] / totals["payers_new"]) if (totals["spend_rub"] and totals["payers_new"]) else None
        total_cpu = (totals["spend_rub"] / totals["users_new"]) if (totals["spend_rub"] and totals["users_new"]) else None
        total_roas_txt = "—" if not totals["spend_rub"] else "%.1f×" % (totals["revenue_period"] / totals["spend_rub"])
        rows += (
            f"<tr style='font-weight:700'><td>Итого</td><td>{totals['users_new']} <span class='meta'>/ {totals['users_total']}</span></td>"
            f"<td>{totals['payers_new']} <span class='meta'>/ {totals['payers_total']}</span></td>"
            f"<td>{_r(totals['revenue_period'])}</td><td>{_r(totals['spend_rub']) if totals['spend_rub'] else '—'}</td>"
            f"<td>{_r(total_cpu)}</td><td>{_r(total_cac)}</td>"
            f"<td>{total_roas_txt}</td></tr>"
        )
        source_options = "".join(
            f'<option value="{html_mod.escape(r["source"])}">{html_mod.escape(r["source"])}</option>'
            for r in econ if r["source"] not in ("(direct)", "(без источника)")
        )
        spend_table = "".join(
            f'<tr><td>{html_mod.escape(r["month"])}</td><td>{html_mod.escape(r["source"] or "— (общие)")}</td><td>{_r(r["spend_rub"])}</td>'
            f'<td>{html_mod.escape(r["note"])}</td><td class="meta">{html_mod.escape(r["updated_by"])} · {html_mod.escape(r["updated_at"])}</td></tr>'
            for r in spend_rows
        )
        bot_username = settings.tg_bot_username or "YOUR_BOT"

        body = f"""
        <div class="card hero">
          <button type="button" class="btn btn-lg" onclick="var b=document.getElementById('utm-builder');b.hidden=!b.hidden;if(!b.hidden)document.getElementById('utm-param').focus()">Собрать ссылку на бота</button>
          <p class="meta">Deep-link вида <code>t.me/{bot_username}?start=источник</code> — параметр запишется источником при первом запуске</p>
          <div id="utm-builder" hidden style="width:100%;max-width:640px;margin-top:8px;display:flex;flex-direction:column;gap:10px;align-items:stretch;text-align:left">
            <div class="filters">
              <label class="f" style="flex:1">Источник (латиница, цифры, _)<input type="text" id="utm-param" placeholder="instagram_bio" maxlength="64" oninput="var v=this.value.replace(/[^A-Za-z0-9_]/g,'');this.value=v;document.getElementById('utm-out').textContent='https://t.me/{bot_username}?start='+(v||'…')"></label>
              <button type="button" class="btn-secondary" onclick="navigator.clipboard.writeText(document.getElementById('utm-out').textContent);this.textContent='Скопировано'">Скопировать</button>
            </div>
            <code id="utm-out" style="font-size:1.05em;padding:10px 12px;background:var(--surface-2);border-radius:10px">https://t.me/{bot_username}?start=…</code>
            <p class="meta" style="margin:0">Не начинайте с <code>@</code> — это рефералы. Источник фиксируется только при первом запуске бота.</p>
          </div>
        </div>

        <div class="card">
          <div class="toolbar">
            <h2 style="margin:0">Экономика источников</h2>
            {pills}
          </div>
          <div class="table-wrap" style="margin-top:12px">
          <table><tr><th>Источник</th><th>Новые / всего</th><th>Платящих</th><th>Выручка</th><th>Расходы</th><th>Регистрация</th><th>CAC</th><th>ROAS</th></tr>
          {rows}</table>
          </div>
          <p class="meta" style="margin-top:8px">CAC — расходы источника за месяцы периода на одного нового платящего; ROAS — выручка периода к расходам.</p>
        </div>

        <div class="card">
          <h2>Расходы на маркетинг</h2>
          {flash}
          <form method="post" action="/admin/marketing-spend" class="filters" style="display:flex">
            <input type="hidden" name="back_to" value="sources"><input type="hidden" name="period" value="{period}">
            <label class="f">Месяц<input type="month" name="month" required></label>
            <label class="f">Источник<select name="source"><option value="">— общие</option>{source_options}</select></label>
            <label class="f">Сумма, ₽<input type="number" name="spend_rub" min="0" step="1" required style="width:130px;min-width:0"></label>
            <label class="f" style="flex:1">Комментарий<input type="text" name="note" maxlength="200"></label>
            <button type="submit">Сохранить</button>
          </form>
          {f'<div class="table-wrap" style="margin-top:12px"><table><tr><th>Месяц</th><th>Источник</th><th>Сумма</th><th>Комментарий</th><th>Кто · когда</th></tr>{spend_table}</table></div>' if spend_table else ''}
        </div>
        """
        return _page("Источники трафика", body)

    # ── Source detail page ──────────────────────────────────────────

    @app.get("/admin/sources/{source:path}", response_class=HTMLResponse)
    async def source_detail(source: str, _user: str = Depends(_check_auth)) -> str:
        src_escaped = html_mod.escape(source)
        users = await credits_db.users_by_source(source)
        tg_ids = [u["tg_id"] for u in users]
        total_users = len(tg_ids)

        funnel_raw, ratings_raw = await asyncio.gather(
            credits_db.funnel_reach_counts_for_users(tg_ids),
            credits_db.rating_distribution_for_users(tg_ids),
        )
        funnel_map = {r["event"]: r["count"] for r in funnel_raw}
        max_funnel = max(funnel_map.values()) if funnel_map else 1
        first_cnt = funnel_map.get(_FUNNEL_ORDER[0], 0) or 1
        funnel_html = ""
        for i, event in enumerate(_FUNNEL_ORDER):
            cnt = funnel_map.get(event, 0)
            pct = max(15, cnt / max_funnel * 100) if max_funnel > 0 else 15
            conv = cnt / first_cnt * 100
            color = _FUNNEL_COLORS[i] if i < len(_FUNNEL_COLORS) else "#8d86ad"
            label = _event_label(event)
            funnel_html += (
                f'<div class="funnel-bar-wrap">'
                f'<div class="funnel-bar" style="width:{pct:.0f}%;background:{color}">'
                f'<span class="flabel">{label}</span>'
                f'<span class="fcount">{cnt} <small>({conv:.0f}%)</small></span>'
                f'</div></div>\n'
            )

        # ── Rating distribution for doughnut chart ──
        rating_map = {r["rating"]: r["count"] for r in ratings_raw}
        src_chart_labels = json.dumps([_RATING_LABELS.get(k, k) for k in ["low", "mid_low", "high"]])
        src_chart_data = json.dumps([rating_map.get(k, 0) for k in ["low", "mid_low", "high"]])
        src_chart_colors = json.dumps([_RATING_COLORS.get(k, "#8d86ad") for k in ["low", "mid_low", "high"]])
        src_total_ratings = sum(rating_map.values())

        revenue = await credits_db.revenue_breakdown_for_users(tg_ids)

        user_rows = ""
        for u in users:
            uname = f"@{u['username']}" if u["username"] else str(u["tg_id"])
            badge = "badge-ok" if u["credits"] > 0 else "badge-zero"
            user_rows += (
                f"<tr><td><a href='/admin/users/{u['tg_id']}'>{uname}</a></td>"
                f"<td>{u['tg_id']}</td>"
                f"<td><span class='badge {badge}'>{u['credits']}</span></td>"
                f"<td>{u['created_at']}</td></tr>"
            )

        src_rows_html = ""
        for i, event in enumerate(_FUNNEL_ORDER):
            cnt = funnel_map.get(event, 0)
            conv = cnt / first_cnt * 100
            color = "var(--ok)" if event in ("payment_confirmed", "subscription_charged") else "var(--accent)"
            src_rows_html += (
                f'<div class="pm-funnel-row"><div>{_event_label(event)}</div>'
                f'<div class="bar"><i style="width:{max(1.0, conv):.0f}%;background:{color}"></i></div>'
                f'<div class="n">{cnt}<span>{conv:.0f}%</span></div></div>'
            )
        bot_username = settings.tg_bot_username or "YOUR_BOT"
        deep_link = f"https://t.me/{bot_username}?start={source}" if source and source != "(direct)" else ""
        src_chart_script = "" if src_total_ratings == 0 else (
            '<script>new Chart(document.getElementById("srcRatingsChart"), {type: "doughnut", data: {labels: ' + src_chart_labels
            + ', datasets: [{data: ' + src_chart_data + ', backgroundColor: ' + src_chart_colors
            + ', borderWidth: 3, borderColor: "#110b20"}]}, options: {responsive: true, maintainAspectRatio: false, cutout: "68%", plugins: {legend: {display: false}}}});</script>'
        )
        rating_kpis = "".join(
            f'<div class="pm-kpi"><div class="l"><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:{_RATING_COLORS[k]};margin-right:6px"></span>{_RATING_LABELS[k]}</div>'
            f'<div class="v" style="font-size:1.5em">{(rating_map.get(k, 0) / src_total_ratings * 100 if src_total_ratings else 0):.0f}%</div><div class="s">{rating_map.get(k, 0)}</div></div>'
            for k in ("high", "mid_low", "low")
        )
        deep_link_html = f' · <code>{html_mod.escape(deep_link)}</code>' if deep_link else ""
        copy_button_html = (
            '<button type="button" class="btn btn-secondary" onclick="navigator.clipboard.writeText(' + json.dumps(deep_link)
            + ');this.textContent=&#39;Скопировано&#39;">Скопировать ссылку</button>'
        ) if deep_link else ""
        body = f"""
        <div class="pm-head">
          <div><h1>{src_escaped}</h1><div class="sub">{total_users} пользователей пришли с этого источника{deep_link_html}</div></div>
          {copy_button_html}
        </div>
        <div class="pm-grid">
          <div class="pm-panel" style="grid-column: span 12">
            <div class="pm-kpis" style="grid-template-columns: repeat(4, minmax(0, 1fr))">
              <div class="pm-kpi"><div class="l">Пользователей</div><div class="v acc">{total_users}</div><div class="s">за всё время</div></div>
              <div class="pm-kpi"><div class="l">Выручка</div><div class="v">{f"{int(revenue.get('confirmed_revenue_rub', 0)):,}".replace(',', ' ')} ₽</div><div class="s">подтверждённые оплаты</div></div>
              <div class="pm-kpi"><div class="l">Ожидает списания</div><div class="v">{f"{int(revenue.get('authorized_revenue_rub', 0)):,}".replace(',', ' ')} ₽</div><div class="s">авторизовано, ещё не списано</div></div>
              <div class="pm-kpi"><div class="l">На пользователя</div><div class="v">{f"{(int(revenue.get('confirmed_revenue_rub', 0)) / total_users if total_users else 0):,.0f}".replace(',', ' ')} ₽</div><div class="s">выручка ÷ пришедшие</div></div>
            </div>
          </div>
          <div class="pm-panel" style="grid-column: span 7; gap: 14px">
            <div class="ph"><b>Воронка <span>% от запустивших бота</span></b></div>
            <div class="pm-funnel">{src_rows_html or '<p class="meta">Нет данных</p>'}</div>
          </div>
          <div class="pm-panel" style="grid-column: span 5; gap: 12px">
            <div class="ph"><b>Оценки видео <span>{src_total_ratings} всего</span></b></div>
            {"<p class='meta'>Оценок пока нет</p>" if src_total_ratings == 0 else f'<div style="display:flex;align-items:center;gap:22px;flex:1;min-height:0"><div class="donut"><canvas id="srcRatingsChart"></canvas></div><div class="stack" style="gap:8px;flex:0 0 auto;min-width:90px">{rating_kpis}</div></div>'}
          </div>
          <div class="pm-panel" style="grid-column: span 12">
            <div class="ph"><b>Пользователи</b></div>
            <div class="table-wrap">
            <table><tr><th>Клиент</th><th>id</th><th>Баланс</th><th>Пришёл</th></tr>
            {user_rows if user_rows else '<tr><td colspan="4">Нет данных</td></tr>'}</table>
            </div>
          </div>
        </div>
        {src_chart_script}
        """
        return _page("", body, back=("Источники", "/admin/sources"))

    # ── Partners (traffic partner accounts + payouts) ────────────────

    @app.get("/admin/partners", response_class=HTMLResponse)
    async def partners_page(request: Request, _user: str = Depends(_check_auth)) -> str:
        partners = await credits_db.list_partners()
        ok_msg = html_mod.escape(str(request.query_params.get("ok", "")).strip())
        err_msg = html_mod.escape(str(request.query_params.get("err", "")).strip())
        flash = ""
        if ok_msg:
            flash += f'<div class="flash flash-ok">{ok_msg}</div>'
        if err_msg:
            flash += f'<div class="flash flash-err">{err_msg}</div>'

        rows = ""
        for p in partners:
            commission = await credits_db.partner_commission_summary(int(p["id"]))
            status_badge = "badge-ok" if p["status"] == "active" else "badge-zero"
            rows += (
                "<tr>"
                f"<td><a href='/admin/partners/{p['id']}'>{html_mod.escape(p['name'] or p['login'])}</a><br>"
                f"<span class='meta'>@{html_mod.escape(p['login'])}</span></td>"
                f"<td><span class='badge {status_badge}'>{html_mod.escape(p['status'])}</span></td>"
                f"<td>{p['users_count']}</td><td>{p['links_count']}</td>"
                f"<td>{commission['due_rub']:,} &#8381;</td>"
                "</tr>"
            )

        body = f"""
        {flash}
        <div class="card">
        <h2>Новый партнёр</h2>
        <form method="post" action="/admin/partners/new" style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
          <label>Логин<br><input type="text" name="login" required></label>
          <label>Пароль<br><input type="text" name="password" required></label>
          <label>Имя<br><input type="text" name="name"></label>
          <button type="submit">Создать</button>
        </form>
        </div>
        <div class="card">
        <h2>Партнёры</h2>
        <div class="table-wrap">
        <table><tr><th>Партнёр</th><th>Статус</th><th>Юзеров</th><th>Ссылок</th><th>К выплате</th></tr>
        {rows if rows else '<tr><td colspan="5">Партнёров пока нет</td></tr>'}
        </table>
        </div>
        </div>
        """
        return _page("Partners", body)

    @app.post("/admin/partners/new")
    async def create_partner(
        login: str = Form(...), password: str = Form(...), name: str = Form(""),
        _user: str = Depends(_check_auth),
    ):
        login_norm = login.strip().lower()
        if not login_norm or not password.strip():
            return RedirectResponse("/admin/partners?err=" + url_quote("Логин и пароль обязательны"), status_code=303)
        if await credits_db.get_partner_by_login(login_norm):
            return RedirectResponse("/admin/partners?err=" + url_quote("Такой логин уже занят"), status_code=303)
        await credits_db.create_partner(login_norm, password, name)
        await credits_db.audit_log(_user, "partner_create", login_norm)
        return RedirectResponse("/admin/partners?ok=" + url_quote(f"Партнёр {login_norm} создан"), status_code=303)

    @app.get("/admin/partners/{partner_id}", response_class=HTMLResponse)
    async def partner_detail(partner_id: int, request: Request, _user: str = Depends(_check_auth)) -> str:
        partner = await credits_db.get_partner(partner_id)
        if not partner:
            raise HTTPException(status_code=404, detail="Partner not found")
        commission = await credits_db.partner_commission_summary(partner_id)
        links = await credits_db.list_partner_links(partner_id)
        payouts = await credits_db.list_partner_payouts(partner_id)
        ok_msg = html_mod.escape(str(request.query_params.get("ok", "")).strip())

        links_rows = "".join(
            f"<tr><td>{html_mod.escape(l['label'] or l['code'])}</td><td><code>{html_mod.escape(l['code'])}</code></td><td>{l['starts_count']}</td></tr>"
            for l in links
        )
        payouts_rows = "".join(
            f"<tr><td>{p['amount_rub']:,} &#8381;</td><td>{html_mod.escape(p['note'])}</td><td>{html_mod.escape(p['created_by'])}</td><td>{p['created_at']}</td></tr>"
            for p in payouts
        )
        toggle_label = "Приостановить" if partner["status"] == "active" else "Активировать"

        body = f"""
        {'<div class="flash flash-ok">' + ok_msg + '</div>' if ok_msg else ''}
        <div class="stat-grid">
          <div class="stat-tile accent"><div class="stat-label">Заработано</div><div class="stat-value">{commission['earned_rub']:,} &#8381;</div></div>
          <div class="stat-tile"><div class="stat-label">Выплачено</div><div class="stat-value">{commission['paid_rub']:,} &#8381;</div></div>
          <div class="stat-tile"><div class="stat-label">К выплате</div><div class="stat-value">{commission['due_rub']:,} &#8381;</div></div>
        </div>
        <div class="card">
        <h2>{html_mod.escape(partner['name'] or partner['login'])} <span class="badge badge-stage">@{html_mod.escape(partner['login'])}</span></h2>
        <form method="post" action="/admin/partners/{partner_id}/toggle-status" style="margin-right:8px">
          <button type="submit" class="btn">{toggle_label}</button>
        </form>
        <form method="post" action="/admin/partners/{partner_id}/reset-password" style="display:inline-flex;gap:8px;align-items:center">
          <input type="text" name="password" placeholder="Новый пароль" required>
          <button type="submit" class="btn-danger">Сбросить пароль</button>
        </form>
        </div>
        <div class="card">
        <h2>Добавить выплату</h2>
        <form method="post" action="/admin/partners/{partner_id}/payout" style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
          <label>Сумма, &#8381;<br><input type="number" name="amount_rub" min="1" required></label>
          <label>Комментарий<br><input type="text" name="note"></label>
          <button type="submit">Записать выплату</button>
        </form>
        <div class="table-wrap">
        <table><tr><th>Сумма</th><th>Комментарий</th><th>Кто добавил</th><th>Дата</th></tr>
        {payouts_rows if payouts_rows else '<tr><td colspan="4">Выплат ещё не было</td></tr>'}
        </table>
        </div>
        </div>
        <div class="card">
        <h2>Ссылки партнёра</h2>
        <div class="table-wrap">
        <table><tr><th>Название</th><th>Код</th><th>Переходов</th></tr>
        {links_rows if links_rows else '<tr><td colspan="3">Ссылок нет</td></tr>'}
        </table>
        </div>
        </div>
        """
        return _page(html_mod.escape(partner['name'] or partner['login']), body, back=("Партнёры", "/admin/partners"))

    @app.post("/admin/partners/{partner_id}/payout")
    async def partner_add_payout(
        partner_id: int, amount_rub: int = Form(...), note: str = Form(""),
        _user: str = Depends(_check_auth),
    ):
        await credits_db.add_partner_payout(partner_id, amount_rub, note, created_by=_user)
        await credits_db.audit_log(_user, "partner_payout", str(partner_id), f"{amount_rub} rub: {note}")
        return RedirectResponse(f"/admin/partners/{partner_id}?ok=" + url_quote("Выплата записана"), status_code=303)

    @app.post("/admin/partners/{partner_id}/reset-password")
    async def partner_reset_password(
        partner_id: int, password: str = Form(...), _user: str = Depends(_check_auth),
    ):
        await credits_db.set_partner_password(partner_id, password)
        await credits_db.audit_log(_user, "partner_reset_password", str(partner_id))
        return RedirectResponse(f"/admin/partners/{partner_id}?ok=" + url_quote("Пароль обновлён"), status_code=303)

    @app.post("/admin/partners/{partner_id}/toggle-status")
    async def partner_toggle_status(partner_id: int, _user: str = Depends(_check_auth)):
        partner = await credits_db.get_partner(partner_id)
        if not partner:
            raise HTTPException(status_code=404, detail="Partner not found")
        new_status = "paused" if partner["status"] == "active" else "active"
        await credits_db.set_partner_status(partner_id, new_status)
        await credits_db.audit_log(_user, "partner_toggle_status", str(partner_id), new_status)
        return RedirectResponse(f"/admin/partners/{partner_id}?ok=" + url_quote(f"Статус: {new_status}"), status_code=303)

    # ── Jobs (stuck/in-flight control) ──────────────────────────────

    @app.get("/admin/jobs", response_class=HTMLResponse)
    async def jobs_page(request: Request, _user: str = Depends(_check_auth)) -> str:
        try:
            min_age_seconds = int(str(request.query_params.get("min_age_seconds", "900")).strip() or "900")
        except Exception:
            min_age_seconds = 900
        try:
            limit = int(str(request.query_params.get("limit", "200")).strip() or "200")
        except Exception:
            limit = 200
        min_age_seconds = max(0, min(min_age_seconds, 604800))
        limit = max(1, min(limit, 500))

        ok_msg = html_mod.escape(str(request.query_params.get("ok", "")).strip())
        err_msg = html_mod.escape(str(request.query_params.get("err", "")).strip())
        data: dict = {}
        if not err_msg:
            try:
                data = await _orchestrator_get_active_jobs(min_age_seconds=min_age_seconds, limit=limit)
            except Exception as e:
                err_msg = html_mod.escape(str(e))

        jobs_obj = data.get("jobs") if isinstance(data, dict) else None
        jobs = jobs_obj if isinstance(jobs_obj, list) else []
        total_active = int(data.get("total_active", 0) or 0) if isinstance(data, dict) else 0

        dozzle_base = str(settings.dozzle_base_url or "").strip().rstrip("/")
        status_ru = {
            "queued": ("в очереди", "info"), "pending": ("в очереди", "info"), "running": ("выполняется", "ok"),
            "building": ("сборка", "ok"), "rendering": ("рендер", "ok"), "polling": ("ждём ноду", "warn"),
            "failed": ("ошибка", "bad"), "done": ("готово", "ok"), "completed": ("готово", "ok"),
        }
        stage_ru = {
            "asr": "распознавание речи", "stage1": "разбор трека", "stage1a": "распознавание речи", "stage1b": "сценарий",
            "stage2": "субтитры и футаж", "stage2_subtitles": "субтитры", "stage2_footage": "подбор футажа",
            "stage3": "сборка проекта", "build": "сборка проекта", "render": "рендер", "render_poll": "ждём рендер",
            "dispatch": "отправка на ноду", "upload": "загрузка результата", "delivery": "доставка",
        }
        rows = ""
        for row in jobs:
            if not isinstance(row, dict):
                continue
            jid_raw = str(row.get("job_id") or "")
            jid = html_mod.escape(jid_raw)
            status_key = str(row.get("status") or "").lower()
            status_label, status_cls = status_ru.get(status_key, (status_key or "—", ""))
            stage_key = str(row.get("stage") or "")
            stage_label = stage_ru.get(stage_key.lower(), stage_key or "—")
            project_id = html_mod.escape(str(row.get("project_id") or ""))
            worker_type = html_mod.escape(str(row.get("llm_worker_type") or ""))
            age_seconds = int(row.get("age_seconds", 0) or 0)
            age_human = _seconds_to_age(age_seconds)
            stale = age_seconds >= max(min_age_seconds, 900)

            logs_cell = ""
            if dozzle_base:
                jid_q = url_quote(jid_raw, safe="")
                logs_cell = (
                    f"<a class='btn btn-secondary btn-sm' href='{dozzle_base}/container/worker-build?search={jid_q}' target='_blank'>сборка</a> "
                    f"<a class='btn btn-secondary btn-sm' href='{dozzle_base}/container/worker-render?search={jid_q}' target='_blank'>рендер</a> "
                    f"<a class='btn btn-secondary btn-sm' href='{dozzle_base}/container/orchestrator-api?search={jid_q}' target='_blank'>оркестратор</a>"
                )

            rows += (
                f"<tr>"
                f"<td><a href='/admin/jobs/{jid}'><code>{jid[:8]}</code></a></td>"
                f"<td><span class='status-pill {status_cls}'><i></i>{html_mod.escape(status_label)}</span></td>"
                f"<td>{html_mod.escape(stage_label)}</td>"
                f"<td>{project_id or '—'}</td>"
                f"<td>{worker_type or '—'}</td>"
                f"<td style='{'color:var(--warn);font-weight:600' if stale else ''}'>{age_human}</td>"
                + (f"<td style='white-space:nowrap'>{logs_cell}</td>" if dozzle_base else "")
                + f"<td style='white-space:nowrap'>"
                f"<form method='post' action='/admin/jobs/{jid}/requeue' style='display:inline' onsubmit=\"return confirm('Перезапустить задачу {jid[:8]}?');\">"
                f"<input type='hidden' name='min_age_seconds' value='{min_age_seconds}'><input type='hidden' name='limit' value='{limit}'>"
                f"<input type='hidden' name='reason' value='admin_requeue'><input type='hidden' name='llm_worker_type' value=''>"
                f"<button type='submit' class='btn-sm btn-secondary'>Перезапустить</button></form> "
                f"<form method='post' action='/admin/jobs/{jid}/kill' style='display:inline' onsubmit=\"return confirm('Остановить задачу {jid[:8]}? Она перейдёт в ошибку.');\">"
                f"<input type='hidden' name='min_age_seconds' value='{min_age_seconds}'><input type='hidden' name='limit' value='{limit}'>"
                f"<input type='hidden' name='reason' value='stuck_job_manual_kill'>"
                f"<button type='submit' class='btn-sm btn-danger'>Остановить</button></form>"
                f"</td></tr>"
            )

        age_options = "".join(
            f'<a href="/admin/jobs?min_age_seconds={sec}&limit={limit}" class="{"on" if min_age_seconds == sec else ""}">{lbl}</a>'
            for sec, lbl in ((0, "все"), (300, "старше 5 мин"), (900, "старше 15 мин"), (3600, "старше часа"))
        )
        stale_count = sum(1 for row in jobs if isinstance(row, dict) and int(row.get("age_seconds", 0) or 0) >= 900)
        flash = ""
        if ok_msg:
            flash = f'<div class="flash flash-ok">{ok_msg}</div>'
        if err_msg:
            flash = f'<div class="flash flash-err">Оркестратор недоступен: {err_msg}</div>'
        colspan = 8 if dozzle_base else 7

        body = f"""
        {flash}
        <div class="card">
          <div class="toolbar">
            <h2 style="margin:0">В работе сейчас</h2>
            <div class="seg">{age_options}</div>
          </div>
          <div class="stage-grid" style="margin-top:14px">
            <div class="stage-chip"><div class="count">{total_active}</div><div class="label">активных задач</div></div>
            <div class="stage-chip"><div class="count" style="color:{'var(--warn)' if stale_count else 'var(--text)'}">{stale_count}</div><div class="label">висят дольше 15 минут</div></div>
            <div class="stage-chip"><div class="count">{len(jobs)}</div><div class="label">показано</div></div>
          </div>
        </div>

        <div class="card">
          <div class="table-wrap">
          <table><tr><th>Задача</th><th>Статус</th><th>Этап</th><th>Проект</th><th>Воркер</th><th>В работе</th>{"<th>Логи</th>" if dozzle_base else ""}<th></th></tr>
          {rows if rows else f'<tr><td colspan="{colspan}">Задач по этому фильтру нет</td></tr>'}</table>
          </div>
          <p class="meta" style="margin-top:10px">«Остановить» переводит задачу в ошибку и снимает её с воркера; клиент в боте возвращается к загрузке аудио. «Перезапустить» ставит задачу в очередь заново.</p>
        </div>
        """
        return _page("Джобы", body)

    @app.post("/admin/jobs/{job_id}/kill")
    async def jobs_kill(
        job_id: str,
        reason: str = Form("stuck_job_manual_kill"),
        min_age_seconds: int = Form(900),
        limit: int = Form(200),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        min_age = max(0, min(int(min_age_seconds), 604800))
        out_limit = max(1, min(int(limit), 500))
        base_q = f"min_age_seconds={min_age}&limit={out_limit}"

        jid = str(job_id or "").strip()
        if not jid:
            return RedirectResponse(f"/admin/jobs?{base_q}&err={quote_plus('empty job_id')}", status_code=303)

        actor_reason = " ".join(f"{reason} by {_user}".split()).strip()
        try:
            res = await _orchestrator_kill_job(job_id=jid, reason=actor_reason)
            project_id = str(res.get("project_id") or "")
            chat_id = _project_chat_id(project_id)
            if chat_id:
                try:
                    await state_store.reset_to_wait_audio(chat_id)
                    await credits_db.log_event(chat_id, "admin_force_reset", f"job={jid} by {_user}")
                except Exception as e:
                    log.warning("jobs_kill: reset_to_wait_audio failed job=%s chat_id=%s err=%s", jid, chat_id, e)
            revoked_ids = res.get("revoked_task_ids")
            revoked_count = len(revoked_ids) if isinstance(revoked_ids, list) else 0
            ok = f"killed job={jid}; revoked={revoked_count}; project={project_id or '-'}"
            return RedirectResponse(f"/admin/jobs?{base_q}&ok={quote_plus(ok)}", status_code=303)
        except Exception as e:
            return RedirectResponse(f"/admin/jobs?{base_q}&err={quote_plus(str(e))}", status_code=303)

    @app.post("/admin/jobs/{job_id}/requeue")
    async def jobs_requeue(
        job_id: str,
        reason: str = Form("admin_requeue"),
        llm_worker_type: str = Form(""),
        min_age_seconds: int = Form(900),
        limit: int = Form(200),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        min_age = max(0, min(int(min_age_seconds), 604800))
        out_limit = max(1, min(int(limit), 500))
        base_q = f"min_age_seconds={min_age}&limit={out_limit}"

        jid = str(job_id or "").strip()
        if not jid:
            return RedirectResponse(f"/admin/jobs?{base_q}&err={quote_plus('empty job_id')}", status_code=303)

        actor_reason = " ".join(f"{reason} by {_user}".split()).strip()
        try:
            res = await _orchestrator_requeue_job(
                job_id=jid,
                reason=actor_reason,
                llm_worker_type=str(llm_worker_type or "").strip(),
            )
            worker = str(res.get("llm_worker_type") or "")
            ok = f"requeued job={jid}; worker={worker or '-'}"
            return RedirectResponse(f"/admin/jobs?{base_q}&ok={quote_plus(ok)}", status_code=303)
        except Exception as e:
            return RedirectResponse(f"/admin/jobs?{base_q}&err={quote_plus(str(e))}", status_code=303)

    # ── Job detail page ───────────────────────────────────────────

    @app.get("/admin/jobs/{job_id}", response_class=HTMLResponse)
    async def job_detail(job_id: str, _user: str = Depends(_check_auth)) -> str:
        jid = str(job_id or "").strip()
        if not jid:
            raise HTTPException(404, "job_id is empty")

        try:
            data = await _orchestrator_get_job(job_id=jid)
        except Exception as e:
            return _page("Job Error", f'<div class="card"><p style="color:#ef6a6a">{html_mod.escape(str(e))}</p></div>')

        if not data:
            raise HTTPException(404, "Job not found")

        status = str(data.get("status") or "")
        stage = str(data.get("stage") or "")
        error = str(data.get("error") or "")
        created_at = float(data.get("created_at", 0) or 0)
        updated_at = float(data.get("updated_at", 0) or 0)
        queued_at = data.get("queued_at")
        started_at = data.get("started_at")
        finished_at = data.get("finished_at")
        request_obj = data.get("request") or {}
        result_obj = data.get("result")
        version = int(data.get("version", 0) or 0)
        idem_key = str(data.get("idempotency_key") or "")

        project_id = str(request_obj.get("project_id") or "")
        worker_type = str(request_obj.get("llm_worker_type") or "")

        import time as _time
        now = _time.time()
        age_seconds = max(0, int(now - updated_at)) if updated_at else 0

        def _ts(ts):
            if not ts:
                return "—"
            from datetime import datetime, timezone
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Status badge color
        status_color = {"SUCCEEDED": "#4ecf8a", "FAILED": "#ef6a6a", "RUNNING": "#5aa9ea", "QUEUED": "#f0b04a", "NEW": "#8d86ad"}.get(status, "#8d86ad")

        # Dozzle log links
        dozzle_base = str(settings.dozzle_base_url or "").strip().rstrip("/")
        logs_html = ""
        if dozzle_base:
            jid_escaped = url_quote(jid, safe="")
            logs_html = f"""
            <div class="card">
            <h3>Логи контейнеров</h3>
            <p>
              <a href="{dozzle_base}/container/worker-build?search={jid_escaped}" target="_blank" rel="noopener">worker-build</a> &nbsp;|&nbsp;
              <a href="{dozzle_base}/container/worker-render?search={jid_escaped}" target="_blank" rel="noopener">worker-render</a> &nbsp;|&nbsp;
              <a href="{dozzle_base}/container/orchestrator-api?search={jid_escaped}" target="_blank" rel="noopener">orchestrator-api</a>
            </p>
            </div>
            """

        # Request/result as formatted JSON
        req_json = html_mod.escape(json.dumps(request_obj, indent=2, ensure_ascii=False, default=str)) if request_obj else "—"
        result_json = html_mod.escape(json.dumps(result_obj, indent=2, ensure_ascii=False, default=str)) if result_obj else "—"

        # Link to user if project_id has tg chat_id
        user_link = ""
        chat_id = _project_chat_id(project_id)
        if chat_id:
            user_link = f' &nbsp;(<a href="/admin/users/{chat_id}">user {chat_id}</a>)'

        error_html = (
            '<div class="card"><h3>Ошибка</h3>'
            f'<pre style="white-space:pre-wrap;color:#ef6a6a">{html_mod.escape(error)}</pre></div>'
        ) if error else ""

        result_html = (
            '<div class="card"><h3>Result</h3>'
            '<pre style="white-space:pre-wrap;max-height:400px;overflow:auto;background:#1a1331;padding:12px;border-radius:6px;font-size:0.85em">'
            f'{result_json}</pre></div>'
        ) if result_obj else ""

        jid_esc = html_mod.escape(jid)
        action_forms: list[str] = []
        if status != "SUCCEEDED":
            action_forms.append(
                f"<form method='post' action='/admin/jobs/{jid_esc}/requeue'"
                f""" onsubmit="return confirm('Requeue job {jid_esc}?');" style='margin-right:10px;margin-bottom:8px'>"""
                "<input type='hidden' name='min_age_seconds' value='0'>"
                "<input type='hidden' name='limit' value='200'>"
                "<input type='text' name='reason' value='admin_requeue' style='width:220px'>"
                "<input type='text' name='llm_worker_type' value='' placeholder='worker(optional)' style='width:160px'>"
                " <button type='submit'>Requeue</button>"
                "</form>"
            )
        if status in ("NEW", "QUEUED", "RUNNING"):
            action_forms.append(
                f"<form method='post' action='/admin/jobs/{jid_esc}/kill'"
                f""" onsubmit="return confirm('Kill job {jid_esc}?');">"""
                "<input type='hidden' name='min_age_seconds' value='0'>"
                "<input type='hidden' name='limit' value='200'>"
                "<input type='text' name='reason' value='stuck_job_manual_kill' style='width:250px'>"
                " <button type='submit' class='btn-danger'>Kill</button>"
                "</form>"
            )
        actions_html = (
            '<div class="card"><h3>Действия</h3>'
            + "".join(action_forms)
            + "</div>"
        ) if action_forms else ""

        body = f"""
        <div class="card">
        <h2>Job <code>{jid_esc}</code></h2>
        <table>
          <tr><td style="width:160px"><strong>Status</strong></td><td><span style="color:{status_color};font-weight:700">{html_mod.escape(status)}</span></td></tr>
          <tr><td><strong>Stage</strong></td><td>{html_mod.escape(stage) or '—'}</td></tr>
          <tr><td><strong>Project</strong></td><td>{html_mod.escape(project_id) or '—'}{user_link}</td></tr>
          <tr><td><strong>Worker type</strong></td><td>{html_mod.escape(worker_type) or '—'}</td></tr>
          <tr><td><strong>Idempotency key</strong></td><td><code>{html_mod.escape(idem_key) or '—'}</code></td></tr>
          <tr><td><strong>Version</strong></td><td>{version}</td></tr>
          <tr><td><strong>Age</strong></td><td>{_seconds_to_age(age_seconds)}</td></tr>
          <tr><td><strong>Created</strong></td><td>{_ts(created_at)}</td></tr>
          <tr><td><strong>Queued</strong></td><td>{_ts(queued_at)}</td></tr>
          <tr><td><strong>Started</strong></td><td>{_ts(started_at)}</td></tr>
          <tr><td><strong>Finished</strong></td><td>{_ts(finished_at)}</td></tr>
          <tr><td><strong>Updated</strong></td><td>{_ts(updated_at)}</td></tr>
        </table>
        </div>

        {error_html}

        {logs_html}

        <div class="card">
        <h3>Request</h3>
        <pre style="white-space:pre-wrap;max-height:400px;overflow:auto;background:#1a1331;padding:12px;border-radius:6px;font-size:0.85em">{req_json}</pre>
        </div>

        {result_html}

        {actions_html}
        """
        return _page(f"Задача {jid[:8]}", body, back=("Джобы", "/admin/jobs"))

    @app.get("/admin/runs", response_class=HTMLResponse)
    async def runs_page(
        request: Request,
        _user: str = Depends(_check_auth),
    ) -> str:
        if runtime_store is None:
            return _page(
                "Runs",
                '<div class="card"><p style="color:#ef6a6a">Generation runtime store is unavailable.</p></div>',
            )

        status = str(request.query_params.get("status") or "").strip()
        scope = str(request.query_params.get("scope") or "active").strip().lower()
        limit = _query_int(request, "limit", default=100, min_value=1, max_value=300)
        include_terminal = scope == "all"

        try:
            runs = await runtime_store.list_runs(
                surface="public",
                status=status,
                include_terminal=include_terminal,
                limit=limit,
                offset=0,
            )
        except Exception as e:
            return _page(
                "Runs",
                f'<div class="card"><p style="color:#ef6a6a">{html_mod.escape(str(e))}</p></div>',
            )

        rows = ""
        for row in runs:
            run_id = str(row.get("run_id") or "")
            chat_id = int(row.get("chat_id") or 0)
            batch_id = str(row.get("batch_id") or "")
            run_status = str(row.get("status") or "")
            current_stage = str(row.get("current_stage") or "")
            versions_total = int(row.get("versions_total") or 0)
            next_ver = int(row.get("next_version_to_enqueue") or 0)
            status_color = {
                "queued": "#f0b04a",
                "running": "#5aa9ea",
                "succeeded": "#4ecf8a",
                "failed": "#ef6a6a",
                "cancelled": "#8d86ad",
            }.get(run_status.lower(), "#8d86ad")
            rows += (
                "<tr>"
                f"<td><a href='/admin/runs/{html_mod.escape(run_id)}'><code>{html_mod.escape(run_id)}</code></a></td>"
                f"<td><a href='/admin/users/{chat_id}'>{chat_id}</a></td>"
                f"<td>{html_mod.escape(batch_id or '—')}</td>"
                f"<td><span style='color:{status_color};font-weight:700'>{html_mod.escape(run_status or '—')}</span></td>"
                f"<td>{html_mod.escape(current_stage or '—')}</td>"
                f"<td>{versions_total}</td>"
                f"<td>{next_ver}</td>"
                f"<td>{_runtime_dt_text(row.get('updated_at'))}</td>"
                f"<td>{_compact_runtime_text(row.get('last_error_text') or row.get('last_error_code') or '')}</td>"
                "</tr>"
            )

        selected_active = " selected" if scope != "all" else ""
        selected_all = " selected" if scope == "all" else ""
        body = f"""
        <div class="card">
          <h2>Generation Runs</h2>
          <form method="get" action="/admin/runs" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <label>Scope:
              <select name="scope">
                <option value="active"{selected_active}>active only</option>
                <option value="all"{selected_all}>all</option>
              </select>
            </label>
            <label>Status: <input type="text" name="status" value="{html_mod.escape(status)}" placeholder="running / failed / succeeded"></label>
            <label>Limit: <input type="number" name="limit" value="{limit}" min="1" max="300"></label>
            <button type="submit">Refresh</button>
          </form>
          <form method="post" action="/admin/alignment-smoke/start"
                style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px">
            <strong>Alignment smoke:</strong>
            <label>Days: <input type="number" name="days" value="30" min="1" max="90"></label>
            <label>Jobs: <input type="number" name="count" value="30" min="1" max="50"></label>
            <button type="submit">Start on orchestrator</button>
          </form>
          <p style="color:#c6bfe4;font-size:0.88em">Runs only local CTC alignment and uploads lightweight previews; no LLM, credits, footage, or render node.</p>
          <p style="margin-top:8px">Visible runs: <strong>{len(runs)}</strong></p>
          <div class="table-wrap">
            <table>
              <tr><th>Run</th><th>Chat</th><th>Batch</th><th>Status</th><th>Stage</th><th>Total</th><th>Next</th><th>Updated</th><th>Last error</th></tr>
              {rows or '<tr><td colspan="9">Нет run по текущему фильтру</td></tr>'}
            </table>
          </div>
          <p style="color:#c6bfe4;font-size:0.88em">Карточка run показывает версии, outbox и event trail в одном месте.</p>
        </div>
        """
        return _page("Runs", body)

    @app.get("/admin/runs-export.json", response_class=JSONResponse)
    async def runs_export(request: Request, _user: str = Depends(_check_auth)) -> JSONResponse:
        """Export recent alignment inputs for deterministic, offline smoke testing."""
        if runtime_store is None:
            raise HTTPException(503, "Generation runtime store is unavailable")

        days = _query_int(request, "days", default=30, min_value=1, max_value=90)
        limit = _query_int(request, "limit", default=100, min_value=1, max_value=300)
        created_after = datetime.now(timezone.utc) - timedelta(days=days)
        rows = await runtime_store.list_alignment_smoke_candidates(
            surface="public",
            created_after=created_after,
            limit=limit,
        )

        runs: list[Dict[str, Any]] = []
        by_run_id: Dict[str, Dict[str, Any]] = {}
        allowed_input_fields = (
            "audio_s3_url",
            "lyrics_text",
            "target_fragment",
            "subtitles_mode",
            "user_clip_start_sec",
            "user_clip_end_sec",
            "versions_total",
        )

        def _iso_datetime(value: object) -> str:
            if isinstance(value, datetime):
                return value.isoformat()
            return str(value or "")

        for row in rows:
            run_id = str(row.get("run_id") or "")
            item = by_run_id.get(run_id)
            if item is None:
                payload = dict(row.get("payload") or {})
                item = {
                    "run_id": run_id,
                    "created_at": _iso_datetime(row.get("run_created_at")),
                    "updated_at": _iso_datetime(row.get("run_updated_at")),
                    "status": str(row.get("run_status") or ""),
                    "current_stage": str(row.get("current_stage") or ""),
                    "last_error_code": str(row.get("last_error_code") or ""),
                    "last_error_text": str(row.get("last_error_text") or ""),
                    "input": {key: payload.get(key) for key in allowed_input_fields},
                    "versions": [],
                }
                by_run_id[run_id] = item
                runs.append(item)
            if row.get("version_index") is not None:
                item["versions"].append(
                    {
                        "version_index": int(row.get("version_index") or 0),
                        "job_id": str(row.get("job_id") or ""),
                        "status": str(row.get("job_status") or ""),
                        "stage": str(row.get("job_stage") or ""),
                        "result_url": str(row.get("result_url") or ""),
                        "archive_url": str(row.get("archive_url") or ""),
                        "error": str(row.get("version_error_text") or ""),
                    }
                )

        return JSONResponse(
            {
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "days": days,
                "requested_run_limit": limit,
                "run_count": len(runs),
                "runs": runs,
            },
            headers={
                "Content-Disposition": 'attachment; filename="alignment-smoke-inputs.json"',
                "Cache-Control": "no-store",
            },
        )

    @app.post("/admin/alignment-smoke/start", response_class=JSONResponse)
    async def alignment_smoke_start(
        days: int = Form(30),
        count: int = Form(30),
        _user: str = Depends(_check_auth),
    ) -> JSONResponse:
        if runtime_store is None:
            raise HTTPException(503, "Generation runtime store is unavailable")
        days = max(1, min(int(days), 90))
        count = max(1, min(int(count), 50))
        created_after = datetime.now(timezone.utc) - timedelta(days=days)
        rows = await runtime_store.list_alignment_smoke_candidates(
            surface="public",
            created_after=created_after,
            limit=300,
        )

        candidates: list[dict[str, Any]] = []
        seen_runs: set[str] = set()
        for row in rows:
            run_id = str(row.get("run_id") or "").strip()
            if not run_id or run_id in seen_runs:
                continue
            payload = dict(row.get("payload") or {})
            audio_s3_url = str(payload.get("audio_s3_url") or "").strip()
            target_fragment = str(payload.get("target_fragment") or "").strip()
            start = payload.get("user_clip_start_sec")
            end = payload.get("user_clip_end_sec")
            try:
                start_f = float(start)
                end_f = float(end)
            except (TypeError, ValueError):
                continue
            if (
                not audio_s3_url.lower().startswith("s3://")
                or not target_fragment
                or start_f < 0.0
                or end_f <= start_f
            ):
                continue
            seen_runs.add(run_id)
            candidates.append(
                {
                    "run_id": run_id,
                    "audio_s3_url": audio_s3_url,
                    "target_fragment": target_fragment,
                    "clip_start_abs": start_f,
                    "clip_end_abs": end_f,
                }
            )

        selected = candidates[:count]
        if not selected:
            raise HTTPException(409, "No complete s3 alignment inputs found")
        batch_id = f"{int(time.time())}-{secrets.token_hex(4)}"
        jobs: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        for item in selected:
            run_id = str(item["run_id"])
            request_payload = {
                "audio_s3_url": item["audio_s3_url"],
                "target_fragment": item["target_fragment"],
                "clip_start_abs": item["clip_start_abs"],
                "clip_end_abs": item["clip_end_abs"],
                "request_id": f"smoke:{batch_id}:{run_id}",
                "idempotency_key": f"alignment-smoke:{batch_id}:{run_id}",
            }
            try:
                enqueued = await _orchestrator_enqueue_alignment_smoke(request_payload)
                jobs.append(
                    {
                        "run_id": run_id,
                        "job_id": str(enqueued.get("job_id") or ""),
                        "status": str(enqueued.get("status") or ""),
                        "created": bool(enqueued.get("created", True)),
                        "preview_path": f"/admin/alignment-smoke/{enqueued.get('job_id')}/preview",
                    }
                )
            except Exception as exc:
                failures.append({"run_id": run_id, "error": str(exc)})

        base = str(settings.orchestrator_public_url or "").strip().rstrip("/")
        return JSONResponse(
            {
                "schema_version": 1,
                "batch_id": batch_id,
                "days": days,
                "requested_count": count,
                "eligible_count": len(candidates),
                "enqueued_count": len(jobs),
                "failed_to_enqueue_count": len(failures),
                "jobs": jobs,
                "failures": failures,
                "poll_url_template": f"{base}/jobs/{{job_id}}",
            },
            headers={
                "Content-Disposition": f'attachment; filename="alignment-smoke-jobs-{batch_id}.json"',
                "Cache-Control": "no-store",
            },
        )

    @app.get("/admin/alignment-smoke/{job_id}/preview")
    async def alignment_smoke_preview(
        job_id: str,
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        jid = str(job_id or "").strip()
        if not jid:
            raise HTTPException(404, "job_id is empty")
        state = await _orchestrator_get_job(job_id=jid)
        if not state:
            raise HTTPException(404, "Alignment smoke job not found")
        request_payload = dict(state.get("request") or {})
        if str(request_payload.get("job_kind") or "") != "alignment_smoke":
            raise HTTPException(404, "Alignment smoke job not found")
        if str(state.get("status") or "").upper() != "SUCCEEDED":
            raise HTTPException(409, "Alignment smoke preview is not ready")
        result = dict(state.get("result") or {})
        output_url = str(result.get("output_url") or "").strip()
        expected_bucket = str(getattr(settings, "s3_bucket_output_video", "") or "").strip()
        expected_key = f"alignment-smoke/{jid}/preview.mp4"
        if output_url != f"s3://{expected_bucket}/{expected_key}" or not expected_bucket:
            raise HTTPException(409, "Alignment smoke preview locator is invalid")
        from src.storage.s3 import generate_presigned_url

        preview_url = generate_presigned_url(
            expected_bucket,
            expected_key,
            expires_in=900,
        )
        return RedirectResponse(
            url=preview_url,
            status_code=307,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/admin/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(run_id: str, _user: str = Depends(_check_auth)) -> str:
        rid = str(run_id or "").strip()
        if not rid:
            raise HTTPException(404, "run_id is empty")
        if runtime_store is None:
            return _page(
                "Run Error",
                '<div class="card"><p style="color:#ef6a6a">Generation runtime store is unavailable.</p></div>',
            )

        run = await runtime_store.get_run(rid)
        if not run:
            raise HTTPException(404, "Run not found")
        versions = await runtime_store.get_versions(rid)
        outbox_rows = await runtime_store.list_outbox_items(surface="public", run_id=rid, limit=200)
        events = await runtime_store.list_events(rid, limit=200)

        versions_html = "".join(
            "<tr>"
            f"<td>{int(row.get('version_index') or 0)}</td>"
            f"<td>{_job_admin_link(row.get('job_id'))}</td>"
            f"<td>{html_mod.escape(str(row.get('job_status') or '—'))}</td>"
            f"<td>{html_mod.escape(str(row.get('job_stage') or '—'))}</td>"
            f"<td>{html_mod.escape(str(row.get('worker_type') or '—'))}</td>"
            f"<td>{html_mod.escape(str(row.get('origin_node') or '—'))}</td>"
            f"<td>{html_mod.escape(str(row.get('build_queue') or '—'))}</td>"
            f"<td>{html_mod.escape(str(row.get('render_queue') or '—'))}</td>"
            f"<td>{_compact_runtime_text(row.get('last_error_text') or '')}</td>"
            "</tr>"
            for row in versions
        )
        outbox_html = "".join(
            "<tr>"
            f"<td><code>{html_mod.escape(str(row.get('dedupe_key') or ''))}</code></td>"
            f"<td>{html_mod.escape(str(row.get('kind') or '—'))}</td>"
            f"<td>{html_mod.escape(str(row.get('status') or '—'))}</td>"
            f"<td>{int(row.get('attempt_count') or 0)}</td>"
            f"<td>{_runtime_dt_text(row.get('next_attempt_at'))}</td>"
            f"<td>{_runtime_dt_text(row.get('sent_at'))}</td>"
            f"<td>{_compact_runtime_text(row.get('last_error') or '')}</td>"
            "</tr>"
            for row in outbox_rows
        )
        events_html = "".join(
            "<tr>"
            f"<td>{_runtime_dt_text(row.get('created_at'))}</td>"
            f"<td>{html_mod.escape(str(row.get('event_type') or '—'))}</td>"
            f"<td>{html_mod.escape(str(row.get('job_id') or '—'))}</td>"
            f"<td><pre style='white-space:pre-wrap;margin:0'>{html_mod.escape(json.dumps(row.get('payload') or {}, ensure_ascii=False, default=str))}</pre></td>"
            "</tr>"
            for row in events
        )

        body = f"""
        <div class="card">
          <h2>Run <code>{html_mod.escape(rid)}</code></h2>
          <table>
            <tr><td style="width:180px"><strong>Surface</strong></td><td>{html_mod.escape(str(run.get('surface') or '—'))}</td></tr>
            <tr><td><strong>Chat</strong></td><td><a href="/admin/users/{int(run.get('chat_id') or 0)}">{int(run.get('chat_id') or 0)}</a></td></tr>
            <tr><td><strong>Batch</strong></td><td>{html_mod.escape(str(run.get('batch_id') or '—'))}</td></tr>
            <tr><td><strong>Status</strong></td><td>{html_mod.escape(str(run.get('status') or '—'))}</td></tr>
            <tr><td><strong>Current stage</strong></td><td>{html_mod.escape(str(run.get('current_stage') or '—'))}</td></tr>
            <tr><td><strong>Versions total</strong></td><td>{int(run.get('versions_total') or 0)}</td></tr>
            <tr><td><strong>Next version</strong></td><td>{int(run.get('next_version_to_enqueue') or 0)}</td></tr>
            <tr><td><strong>Last error code</strong></td><td>{html_mod.escape(str(run.get('last_error_code') or '—'))}</td></tr>
            <tr><td><strong>Last error text</strong></td><td>{_compact_runtime_text(run.get('last_error_text') or '', limit=300)}</td></tr>
            <tr><td><strong>Created</strong></td><td>{_runtime_dt_text(run.get('created_at'))}</td></tr>
            <tr><td><strong>Updated</strong></td><td>{_runtime_dt_text(run.get('updated_at'))}</td></tr>
          </table>
        </div>

        <div class="card">
          <h3>Versions</h3>
          <div class="table-wrap">
            <table>
              <tr><th>#</th><th>Job</th><th>Status</th><th>Stage</th><th>Worker</th><th>Origin</th><th>Build queue</th><th>Render queue</th><th>Last error</th></tr>
              {versions_html or '<tr><td colspan="9">Нет versions</td></tr>'}
            </table>
          </div>
        </div>

        <div class="card">
          <h3>Outbox</h3>
          <div class="table-wrap">
            <table>
              <tr><th>Dedupe key</th><th>Kind</th><th>Status</th><th>Attempts</th><th>Next attempt</th><th>Sent at</th><th>Last error</th></tr>
              {outbox_html or '<tr><td colspan="7">Нет outbox items</td></tr>'}
            </table>
          </div>
        </div>

        <div class="card">
          <h3>Events</h3>
          <div class="table-wrap">
            <table>
              <tr><th>At</th><th>Type</th><th>Job</th><th>Payload</th></tr>
              {events_html or '<tr><td colspan="4">Нет events</td></tr>'}
            </table>
          </div>
        </div>
        """
        return _page(f"Прогон {rid[:8]}", body, back=("Прогоны", "/admin/runs"))

    # ── Runtime config ────────────────────────────────────────────────

    @app.get("/admin/runtime-config", response_class=HTMLResponse)
    async def runtime_config_page(request: Request, _user: str = Depends(_check_auth)) -> str:
        ok_msg = html_mod.escape(str(request.query_params.get("ok", "")).strip())
        err_msg = html_mod.escape(str(request.query_params.get("err", "")).strip())
        data: dict = {}
        metrics: dict = {}
        if not err_msg:
            try:
                data = await _orchestrator_get_runtime_config()
                metrics = await _safe_get_metrics()
            except Exception as e:
                err_msg = html_mod.escape(str(e))

        items_obj = data.get("items") if isinstance(data, dict) else None
        items = items_obj if isinstance(items_obj, list) else []
        policy = (
            metrics.get("runtime_capacity_policy")
            if isinstance(metrics.get("runtime_capacity_policy"), dict)
            else {}
        )
        if not policy:
            raw_policy = metrics.get("capacity_policy") if isinstance(metrics.get("capacity_policy"), dict) else {}
            policy = (
                raw_policy.get("runtime_config_snapshot")
                if isinstance(raw_policy.get("runtime_config_snapshot"), dict)
                else raw_policy
            )
        signals = policy.get("signals") if isinstance(policy.get("signals"), dict) else {}
        thresholds = policy.get("thresholds") if isinstance(policy.get("thresholds"), dict) else {}
        actions = policy.get("operator_actions") if isinstance(policy.get("operator_actions"), list) else []
        reasons = policy.get("reasons") if isinstance(policy.get("reasons"), list) else []
        state = html_mod.escape(str(policy.get("state") or "unknown"))

        def _input_html(item: dict[str, object]) -> str:
            key = html_mod.escape(str(item.get("key") or ""))
            kind = str(item.get("kind") or "str")
            value = item.get("value")
            if kind == "bool":
                selected_true = " selected" if bool(value) else ""
                selected_false = " selected" if not bool(value) else ""
                return (
                    f"<select name='{key}'>"
                    f"<option value='1'{selected_true}>on</option>"
                    f"<option value='0'{selected_false}>off</option>"
                    f"</select>"
                )
            if kind == "str":
                value_esc = html_mod.escape(str(value or ""))
                max_len = int(item.get("max_length", 500) or 500)
                return (
                    f"<textarea name='{key}' rows='3' maxlength='{max_len}' "
                    f"style='width:100%;min-width:320px'>{value_esc}</textarea>"
                )
            value_esc = html_mod.escape(str(value if value is not None else ""))
            min_attr = ""
            max_attr = ""
            if item.get("min_value") is not None:
                min_attr = f" min='{html_mod.escape(str(item.get('min_value')))}'"
            if item.get("max_value") is not None:
                max_attr = f" max='{html_mod.escape(str(item.get('max_value')))}'"
            step = " step='0.01'" if kind == "float" else ""
            return f"<input type='number' name='{key}' value='{value_esc}'{min_attr}{max_attr}{step}>"

        rows = ""
        for item_raw in items:
            if not isinstance(item_raw, dict):
                continue
            key = html_mod.escape(str(item_raw.get("key") or ""))
            title = html_mod.escape(str(item_raw.get("title") or key))
            category = html_mod.escape(str(item_raw.get("category") or ""))
            effect = html_mod.escape(str(item_raw.get("runtime_effect") or ""))
            default = html_mod.escape(str(item_raw.get("default") if item_raw.get("default") is not None else ""))
            desc = html_mod.escape(str(item_raw.get("description") or ""))
            is_default = bool(item_raw.get("is_default", True))
            rows += (
                "<tr>"
                f"<td><strong>{title}</strong><br><code>{key}</code><br><span style='color:#c6bfe4'>{desc}</span></td>"
                f"<td>{category}</td>"
                f"<td>{effect}</td>"
                f"<td>{_input_html(item_raw)}</td>"
                f"<td><code>{default}</code></td>"
                f"<td>{'default' if is_default else 'override'}</td>"
                "</tr>"
            )

        signal_rows = "".join(
            f"<tr><td>{html_mod.escape(str(k))}</td><td><code>{html_mod.escape(str(v))}</code></td></tr>"
            for k, v in sorted(signals.items())
        )
        threshold_rows = "".join(
            f"<tr><td>{html_mod.escape(str(k))}</td><td><code>{html_mod.escape(str(v))}</code></td></tr>"
            for k, v in sorted(thresholds.items())
        )
        action_html = "".join(f"<li>{html_mod.escape(str(a))}</li>" for a in actions)
        reason_html = "".join(f"<li>{html_mod.escape(str(r))}</li>" for r in reasons)

        body = f"""
        <div class="card">
        <h2>Backpressure policy</h2>
        {f"<p style='color:#4ecf8a'><strong>OK:</strong> {ok_msg}</p>" if ok_msg else ""}
        {f"<p style='color:#ef6a6a'><strong>Ошибка:</strong> {err_msg}</p>" if err_msg else ""}
        <p>State: <strong>{state}</strong></p>
        <div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">
          <div class="table-wrap" style="flex:1 1 300px">
            <h3>Signals</h3>
            <table><tr><th>Signal</th><th>Value</th></tr>{signal_rows or '<tr><td colspan="2">Нет данных</td></tr>'}</table>
          </div>
          <div class="table-wrap" style="flex:1 1 300px">
            <h3>Thresholds</h3>
            <table><tr><th>Threshold</th><th>Value</th></tr>{threshold_rows or '<tr><td colspan="2">Нет данных</td></tr>'}</table>
          </div>
        </div>
        <h3>Operator hints</h3>
        {f"<ul>{reason_html}</ul>" if reason_html else "<p>Причин деградации нет.</p>"}
        {f"<ul>{action_html}</ul>" if action_html else "<p>Действий не требуется.</p>"}
        </div>

        <div class="card">
        <h2>Runtime knobs</h2>
        <p style="color:#c6bfe4">Hot параметры применяются сразу в orchestrator. Параметры с <code>requires_*</code> сейчас являются operator-visible target values и требуют recreate соответствующего сервиса.</p>
        <form method="post" action="/admin/runtime-config">
          <div class="table-wrap">
          <table><tr><th>Key</th><th>Category</th><th>Effect</th><th>Value</th><th>Default</th><th>Source</th></tr>
          {rows if rows else '<tr><td colspan="6">Нет данных</td></tr>'}
          </table>
          </div>
          <p><button type="submit" class="btn-success">Apply Runtime Config</button></p>
        </form>
        </div>
        """
        return _page("Runtime Config", body)

    @app.post("/admin/runtime-config")
    async def runtime_config_update(request: Request, _user: str = Depends(_check_auth)) -> RedirectResponse:
        try:
            current = await _orchestrator_get_runtime_config()
            items_obj = current.get("items") if isinstance(current, dict) else None
            items = items_obj if isinstance(items_obj, list) else []
            form = await request.form()
            values: dict[str, object] = {}
            for item_raw in items:
                if not isinstance(item_raw, dict):
                    continue
                key = str(item_raw.get("key") or "").strip()
                if not key:
                    continue
                values[key] = form.get(key)
            await _orchestrator_put_runtime_config({"values": values})
            return RedirectResponse(
                f"/admin/runtime-config?ok={quote_plus('runtime config updated')}",
                status_code=303,
            )
        except Exception as e:
            return RedirectResponse(
                f"/admin/runtime-config?err={quote_plus(str(e))}",
                status_code=303,
            )

    # ── LLM workers runtime control ────────────────────────────────

    @app.get("/admin/llm-workers", response_class=HTMLResponse)
    async def llm_workers_page(_user: str = Depends(_check_auth)) -> str:
        err = ""
        data: dict = {}
        try:
            data = await _orchestrator_get_llm_workers()
        except Exception as e:
            err = html_mod.escape(str(e))

        workers_obj = data.get("workers") if isinstance(data, dict) else None
        workers = workers_obj if isinstance(workers_obj, dict) else {}
        order = tuple(str(wt) for wt in LLM_WORKER_TYPES)
        runtime_warnings = _llm_workers_runtime_warnings(
            {
                wt: row if isinstance(row, dict) else {}
                for wt, row in workers.items()
            }
        )

        rows = ""
        for wt in order:
            row = workers.get(wt) if isinstance(workers.get(wt), dict) else {}
            enabled = bool(row.get("enabled", False))
            weight = int(row.get("weight", 0) or 0)
            max_inflight = int(row.get("max_inflight", 1) or 1)
            inflight = int(row.get("inflight", 0) or 0)
            slots = int(row.get("available_slots", max(0, max_inflight - inflight)) or 0)
            rows += (
                f"<tr>"
                f"<td><strong>{wt}</strong></td>"
                f"<td>{'on' if enabled else 'off'}</td>"
                f"<td>{weight}</td>"
                f"<td>{max_inflight}</td>"
                f"<td>{inflight}</td>"
                f"<td>{slots}</td>"
                f"</tr>"
            )

        def _enabled_select(name: str, selected: bool) -> str:
            on_sel = " selected" if selected else ""
            off_sel = " selected" if not selected else ""
            return (
                f'<select name="{name}">'
                f'<option value="1"{on_sel}>on</option>'
                f'<option value="0"{off_sel}>off</option>'
                f"</select>"
            )

        form_rows = ""
        for wt in order:
            row = workers.get(wt) if isinstance(workers.get(wt), dict) else {}
            enabled = bool(row.get("enabled", False))
            weight = int(row.get("weight", 1) or 1)
            max_inflight = int(row.get("max_inflight", 4) or 4)
            form_rows += (
                f"<tr>"
                f"<td><strong>{wt}</strong></td>"
                f"<td>{_enabled_select(f'{wt}_enabled', enabled)}</td>"
                f"<td><input type='number' name='{wt}_weight' value='{weight}' min='0' max='1000'></td>"
                f"<td><input type='number' name='{wt}_max_inflight' value='{max_inflight}' min='1' max='1000'></td>"
                f"</tr>"
            )

        body = f"""
        <div class="card">
        <h2>Текущий runtime статус</h2>
        {f"<p style='color:#ef6a6a'><strong>Ошибка:</strong> {err}</p>" if err else ""}
        {"".join(f"<p style='color:#ef6a6a'><strong>Warning:</strong> {html_mod.escape(w)}</p>" for w in runtime_warnings)}
        <div class="table-wrap">
        <table><tr><th>Worker</th><th>Enabled</th><th>Weight</th><th>Max inflight</th><th>Inflight</th><th>Free slots</th></tr>
        {rows if rows else '<tr><td colspan="6">Нет данных</td></tr>'}</table>
        </div>
        </div>

        <div class="card">
        <h2>Обновить конфиг</h2>
        <form method="post" action="/admin/llm-workers">
          <div class="table-wrap">
          <table><tr><th>Worker</th><th>Enabled</th><th>Weight</th><th>Max inflight</th></tr>
          {form_rows}
          </table>
          </div>
          <p><button type="submit" class="btn-success">Apply Runtime Config</button></p>
        </form>
        </div>
        """
        return _page("LLM Workers", body)

    @app.post("/admin/llm-workers")
    async def llm_workers_update(request: Request, _user: str = Depends(_check_auth)) -> RedirectResponse:
        form = await request.form()
        workers_payload: dict[str, dict[str, object]] = {}
        for wt in LLM_WORKER_TYPES:
            enabled_raw = str(form.get(f"{wt}_enabled", "0")).strip().lower()
            enabled = enabled_raw in {"1", "true", "yes", "on"}
            try:
                weight = int(str(form.get(f"{wt}_weight", "1")).strip() or "1")
            except Exception:
                weight = 1
            try:
                max_inflight = int(str(form.get(f"{wt}_max_inflight", "4")).strip() or "4")
            except Exception:
                max_inflight = 4
            workers_payload[wt] = {
                "enabled": bool(enabled),
                "weight": max(0, int(weight)),
                "max_inflight": max(1, int(max_inflight)),
            }
        payload = {"workers": workers_payload}
        await _orchestrator_put_llm_workers(payload)
        return RedirectResponse("/admin/llm-workers", status_code=303)

    # ── T-Bank webhook (no auth — called by T-Bank servers) ───────

    @app.post("/api/tbank/notify")
    async def tbank_notify(request: Request):
        from fastapi.responses import PlainTextResponse
        try:
            data = await request.json()
        except Exception:
            return PlainTextResponse("OK", status_code=200)

        log.info(
            "tbank notify: order=%s status=%s payment_id=%s rebill_present=%s",
            str(data.get("OrderId", "")),
            str(data.get("Status", "")),
            str(data.get("PaymentId", "")),
            bool(str(data.get("RebillId", "")).strip()),
        )

        # Verify token — reject if no client configured or invalid signature
        if not tbank_client:
            log.warning("tbank notify: no tbank_client configured, rejecting")
            return PlainTextResponse("OK", status_code=200)
        if not tbank_client.verify_notification(data):
            log.warning("tbank notify: invalid token")
            return PlainTextResponse("OK", status_code=200)

        order_id = str(data.get("OrderId", ""))
        status = str(data.get("Status", "")).strip().upper()
        payment_id = str(data.get("PaymentId", ""))
        # For recurrent payments T-Bank puts RebillId in the notification body
        # (GetState never returns it; GetCardList is the only after-the-fact
        # source). Capture it here.
        notif_rebill_id = str(data.get("RebillId", "")).strip()

        if not order_id:
            return PlainTextResponse("OK", status_code=200)

        existing_payment = await credits_db.get_payment(order_id)

        # Persist RebillId the moment it appears, whatever the payment status.
        # T-Bank puts it in the AUTHORIZED notification; for a one-stage payment
        # the CONFIRMED one may arrive without it. Waiting for CONFIRMED before
        # storing the key means an AUTHORIZED-only RebillId is dropped and the
        # subscription can never be charged. Creating the *subscription* still
        # waits for CONFIRMED (below) — only the key itself is saved eagerly.
        if (
            notif_rebill_id
            and existing_payment
            and bool(existing_payment.get("is_recurrent", False))
            and not str(existing_payment.get("rebill_id", "") or "").strip()
        ):
            try:
                await credits_db.update_rebill_id(order_id, notif_rebill_id)
                existing_payment = await credits_db.get_payment(order_id)
                log.info(
                    "tbank notify: rebill captured order=%s status=%s rebill=***%s",
                    order_id, status, notif_rebill_id[-6:],
                )
            except Exception as e:
                # Key in hand but unsaved — make T-Bank replay rather than lose
                # it, same contract as the bootstrap failure below.
                log.error(
                    "tbank notify: rebill capture FAILED order=%s err=%s "
                    "(returning 500 to trigger T-Bank retry)",
                    order_id, e,
                )
                return PlainTextResponse(f"rebill capture failed: {e}", status_code=500)

        async def _bootstrap_recurrent_from_rebill(payment_row: dict | None, *, warn_missing: bool = False):
            if not payment_row or not bool(payment_row.get("is_recurrent", False)):
                return None
            if str(payment_row.get("status", "")).strip().upper() != "CONFIRMED":
                return None
            # A RebillId already captured from an earlier notification (T-Bank
            # sends it on AUTHORIZED; the CONFIRMED twin may omit it) is just as
            # good as one in the body we are handling right now.
            effective_rebill_id = notif_rebill_id or str(payment_row.get("rebill_id", "") or "").strip()
            if not effective_rebill_id:
                if warn_missing:
                    log.warning(
                        "tbank notify: is_recurrent payment confirmed but RebillId absent "
                        "from notification \u2014 user will not be auto-charged. order=%s",
                        order_id,
                    )
                return None

            try:
                tg_id_boot = int(payment_row["tg_id"])
                pkg_boot = str(payment_row["package"])
                amount_boot = int(payment_row["amount_rub"])
                masked_rebill_id = f"***{effective_rebill_id[-6:]}"
                if not str(payment_row.get("rebill_id", "")).strip():
                    await credits_db.update_rebill_id(order_id, effective_rebill_id)
                active_sub = await credits_db.get_active_subscription(tg_id_boot)
                if not active_sub:
                    await credits_db.create_subscription(
                        tg_id_boot, pkg_boot, effective_rebill_id, amount_boot,
                    )
                    await credits_db.log_event(
                        tg_id_boot, "subscription_created", f"{pkg_boot} rebill={masked_rebill_id}",
                    )
                    log.info(
                        "tbank notify: subscription bootstrap ok order=%s rebill=%s",
                        order_id, masked_rebill_id,
                    )
                else:
                    log.info(
                        "tbank notify: subscription bootstrap skipped order=%s active_sub=%s rebill=%s",
                        order_id, active_sub.get("id", "?"), masked_rebill_id,
                    )
            except Exception as e:
                # RebillId was received but we failed to persist it. Return 500
                # so T-Bank retries the notification rather than dropping it.
                log.error(
                    "tbank notify: subscription bootstrap FAILED order=%s err=%s "
                    "(returning 500 to trigger T-Bank retry)",
                    order_id, e,
                )
                return PlainTextResponse(f"bootstrap failed: {e}", status_code=500)
            return None

        # Resolve the entitlement before persisting CONFIRMED.  Unknown package
        # values must be retried/inspected, never silently credited as an old
        # five-video tariff.
        confirmed_credits = None
        if status == "CONFIRMED" and existing_payment:
            try:
                confirmed_credits = package_video_credits(existing_payment["package"])
            except ValueError as exc:
                log.error("tbank notify: %s order=%s", exc, order_id)
                return PlainTextResponse(str(exc), status_code=500)

        # Dedup check
        if payment_id and await credits_db.is_payment_processed(payment_id, status):
            log.info("tbank notify: duplicate payment_id=%s status=%s", payment_id, status)
            bootstrap_resp = await _bootstrap_recurrent_from_rebill(existing_payment)
            if bootstrap_resp is not None:
                return bootstrap_resp
            return PlainTextResponse("OK", status_code=200)

        current_status = str(existing_payment.get("status", "")).strip().upper() if existing_payment else ""
        should_apply_status = _should_apply_payment_status_update(current_status, status)
        confirmation = None
        if should_apply_status:
            if status == "CONFIRMED":
                try:
                    confirmation = await credits_db.confirm_payment_once(
                        order_id,
                        payment_id,
                        actor="tbank_webhook",
                    )
                except ValueError as exc:
                    log.error("tbank notify: confirmation failed order=%s err=%s", order_id, exc)
                    return PlainTextResponse(str(exc), status_code=500)
                should_apply_status = bool(confirmation.get("applied", False))
                payment = await credits_db.get_payment(order_id)
            else:
                await credits_db.update_payment_status(order_id, status, payment_id)
                payment = await credits_db.get_payment(order_id)
        else:
            payment = existing_payment
            log.info(
                "tbank notify: ignored status downgrade order=%s current=%s incoming=%s payment_id=%s",
                order_id,
                current_status,
                status,
                payment_id,
            )

        effective_status = str(payment.get("status", status) if payment else status).strip().upper()
        bootstrap_resp = await _bootstrap_recurrent_from_rebill(payment)
        if bootstrap_resp is not None:
            return bootstrap_resp

        # Get payment info for notifications
        tg_id = payment["tg_id"] if payment else 0
        pkg = payment["package"] if payment else "?"
        amount_rub = payment["amount_rub"] if payment else 0

        # Notify manager about every status change
        if should_apply_status and bot_ref and bot_ref[0] and settings.manager_chat_id and payment:
            status_labels = {
                "CONFIRMED": "Оплачено",
                "AUTHORIZED": "Авторизовано",
                "REJECTED": "Отклонено",
                "REFUNDED": "Возврат",
                "REVERSED": "Отмена",
                "NEW": "Создан",
            }
            status_label = status_labels.get(effective_status, effective_status)
            emoji = {"CONFIRMED": "\u2705", "REJECTED": "\u274c", "REFUNDED": "\U0001f504", "REVERSED": "\U0001f504"}.get(effective_status, "\U0001f4cb")
            try:
                user_info = await credits_db.get_user(tg_id)
                uname = f"@{user_info['username']}" if user_info and user_info.get("username") else str(tg_id)
                await bot_ref[0].send_message(
                    settings.manager_chat_id,
                    f"{emoji} Статус оплаты: {status_label}\n\n"
                    f"Пользователь: {uname}\n"
                    f"Пакет: {pkg}\n"
                    f"Сумма: {amount_rub}\u20bd\n"
                    f"Order: {order_id}",
                )
            except Exception as e:
                log.warning("tbank notify: failed to notify manager: %s", e)

        # On confirmed payment — grant credits & redirect to generation
        if should_apply_status and effective_status == "CONFIRMED" and current_status != "CONFIRMED" and payment:
            credits_to_add = int(
                confirmation.get("credits_added", confirmed_credits or 0)
                if confirmation
                else (confirmed_credits or 0)
            )
            await credits_db.log_event(tg_id, "payment_confirmed", f"{pkg} \u2014 {amount_rub}\u20bd")
            # Subscription bootstrap for recurrent payments. RebillId arrives in
            # a notification body (GetState never returns it) and can go missing
            # if a webhook was lost; without it the rebill loop has no key to
            # charge against, so fall back to GetCardList and \u2014 if even that is
            # empty \u2014 page the manager instead of failing silently.
            is_recurrent_pay = bool(payment.get("is_recurrent", False))
            if is_recurrent_pay and not notif_rebill_id:
                await _bootstrap_recurrent_from_rebill(payment, warn_missing=True)
                if not str((await credits_db.get_payment(order_id) or {}).get("rebill_id", "") or "").strip():
                    recovered = ""
                    if tbank_client:
                        try:
                            recovered = await tbank_client.find_rebill_id(str(tg_id))
                        except Exception as e:
                            log.warning("tbank notify: GetCardList recovery failed order=%s err=%s", order_id, e)
                    if recovered:
                        await credits_db.update_rebill_id(order_id, recovered)
                        refreshed = await credits_db.get_payment(order_id)
                        await _bootstrap_recurrent_from_rebill(refreshed)
                        log.info(
                            "tbank notify: rebill recovered via GetCardList order=%s rebill=***%s",
                            order_id, recovered[-6:],
                        )
                    else:
                        log.error(
                            "tbank notify: recurrent payment CONFIRMED with no RebillId anywhere "
                            "(notification + GetCardList) \u2014 no autopay. order=%s tg_id=%s",
                            order_id, tg_id,
                        )
                        if bot_ref and bot_ref[0] and settings.manager_chat_id:
                            try:
                                await bot_ref[0].send_message(
                                    settings.manager_chat_id,
                                    "\u26a0\ufe0f \u041f\u043e\u0434\u043f\u0438\u0441\u043a\u0430 \u0431\u0435\u0437 \u0430\u0432\u0442\u043e\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044f\n\n"
                                    f"\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c: {tg_id}\n"
                                    f"\u041f\u0430\u043a\u0435\u0442: {pkg}\n"
                                    f"\u0421\u0443\u043c\u043c\u0430: {amount_rub}\u20bd\n"
                                    f"Order: {order_id}\n\n"
                                    "\u041e\u043f\u043b\u0430\u0442\u0430 \u043f\u0440\u043e\u0448\u043b\u0430, \u043d\u043e T-Bank \u043d\u0435 \u0434\u0430\u043b RebillId \u2014 \u043a\u0430\u0440\u0442\u0430 \u043d\u0435 \u043f\u0440\u0438\u0432\u044f\u0437\u0430\u043b\u0430\u0441\u044c "
                                    "(\u043e\u0431\u044b\u0447\u043d\u043e \u0421\u0411\u041f/QR \u0438\u043b\u0438 T-Pay). \u0410\u0432\u0442\u043e\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044f \u043d\u0435 \u0431\u0443\u0434\u0435\u0442. "
                                    "\u041f\u0440\u043e\u0432\u0435\u0440\u044c /admin/subscriptions.",
                                )
                            except Exception as e:
                                log.warning("tbank notify: no-rebill alert failed: %s", e)

            try:
                await state_store.reset_to_wait_audio(tg_id)
            except Exception as e:
                log.warning("tbank notify: failed to unlock user state %s: %s", tg_id, e)
            log.info("payment confirmed tg_id=%s pkg=%s credits=+%s", tg_id, pkg, credits_to_add)

            # Notify user as side-effect. Unlock is already committed.
            if bot_ref and bot_ref[0]:
                try:
                    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
                    bal = await credits_db.get_balance(tg_id)
                    await bot_ref[0].send_message(
                        tg_id,
                        f"\u2705 Оплата прошла! Пакет \u00ab{pkg}\u00bb активирован.\n"
                        f"Начислено {credits_to_add} генераций.\n\n"
                        f"Доступно генераций: {bal}\n\n"
                        "Отправь трек аудио-файлом, и я соберу клип.",
                        reply_markup=ReplyKeyboardMarkup(
                            keyboard=[[KeyboardButton(text="Отправить трек")]],
                            resize_keyboard=True,
                        ),
                    )
                    await bot_ref[0].send_message(tg_id, "Пришли аудио в формате mp3.")
                except Exception as e:
                    log.warning("tbank notify: failed to notify user %s: %s", tg_id, e)

            # Отправить доход в finance-bot
            try:
                user_info_fb = await credits_db.get_user(tg_id)
                uname_fb = f"@{user_info_fb['username']}" if user_info_fb and user_info_fb.get("username") else str(tg_id)
                fb_url = settings.finance_bot_url.rstrip("/") + "/webhook/income"
                async with httpx.AsyncClient(timeout=5) as cli:
                    await cli.post(fb_url, json={"amount": amount_rub, "source": "blast", "client": f"{uname_fb} — {pkg}"})
            except Exception as e:
                log.warning("tbank notify: finance_bot income failed: %s", e)

        return PlainTextResponse("OK", status_code=200)

    # ══════════════════════════════════════════════════════════════════
    # Broadcasts, CRM, Cohorts, Lifecycle, Audit
    # ══════════════════════════════════════════════════════════════════

    def _aud_summary(audience: dict) -> str:
        mode = str(audience.get("mode") or "all")
        if mode == "all":
            return "вся база"
        if mode == "tier":
            val = str((audience.get("tier") or {}).get("value") or "")
            return f"Тир: {val or '—'}"
        if mode == "source":
            val = str((audience.get("source") or {}).get("value") or "")
            return f"Источник: {val or '— любой —'}"
        if mode == "utm":
            utm = audience.get("utm") or {}
            src = str(utm.get("source") or "")
            return f"Источник (legacy): {src or '(пусто)'}"
        if mode == "filter":
            f = audience.get("filter") or {}
            parts = []
            if f.get("credits_min") not in (None, ""):
                parts.append(f"credits≥{f['credits_min']}")
            if f.get("credits_max") not in (None, ""):
                parts.append(f"credits≤{f['credits_max']}")
            if f.get("paid") == "yes":
                parts.append("платили")
            elif f.get("paid") == "no":
                parts.append("не платили")
            if f.get("generated") == "yes":
                parts.append("генерили")
            elif f.get("generated") == "no":
                parts.append("не генерили")
            if f.get("tag"):
                parts.append(f"tag={f['tag']}")
            if f.get("created_from"):
                parts.append(f"с {f['created_from']}")
            if f.get("created_to"):
                parts.append(f"до {f['created_to']}")
            return "Фильтр: " + (", ".join(parts) if parts else "(все)")
        if mode == "manual":
            m = audience.get("manual") or {}
            ids_n = len(m.get("tg_ids") or [])
            un_n = len(m.get("usernames") or [])
            return f"Вручную: {ids_n} id + {un_n} username"
        return mode

    def _parse_manual_list(raw: str) -> dict:
        tg_ids: list[int] = []
        usernames: list[str] = []
        for token in str(raw or "").replace(",", "\n").split():
            t = token.strip().lstrip("@")
            if not t:
                continue
            if t.lstrip("-").isdigit():
                try:
                    tg_ids.append(int(t))
                except Exception:
                    pass
            else:
                usernames.append(t.lower())
        return {"tg_ids": tg_ids, "usernames": usernames}

    def _parse_buttons(raw: str) -> list[dict]:
        out: list[dict] = []
        for line in str(raw or "").splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            text, url = line.split("|", 1)
            text = text.strip()
            url = url.strip()
            if text and url:
                out.append({"text": text, "url": url})
        return out

    def _format_buttons(buttons: list[dict]) -> str:
        return "\n".join(f"{b.get('text','')}|{b.get('url','')}" for b in (buttons or []))

    def _health_label(metrics: dict, credits: int) -> tuple[str, str]:
        """Return (label, css_class) for health score.
        Based on days since last activity + generations last 30d + paid.
        """
        last = metrics.get("_last_activity_raw")
        if last is None:
            return ("Новый", "badge-stage")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            dt = last if isinstance(last, datetime) else None
            days = (now - dt).days if dt else 999
        except Exception:
            days = 999
        gens = int(metrics.get("gens_done_30d") or 0)
        paid = int(metrics.get("paid_orders") or 0)
        if days <= 7 and (gens >= 1 or credits >= 3):
            return ("Активен", "badge-ok")
        if days <= 21:
            return ("Остывает", "badge-stage")
        if paid > 0:
            return ("Потерян", "badge-zero")
        return ("Холодный", "badge-zero")

    def _purchases_html(data: dict) -> str:
        items = data.get("purchases") or []
        sub = data.get("active_subscription")
        if not items and not sub:
            return '<p style="color:#8d86ad">Без покупок</p>'

        sub_html = ""
        if sub:
            pkg_lbl = _PACKAGES.get(str(sub["package"]), f"package={sub['package']}")
            sub_html = (
                f'<div style="border-left:3px solid #4ecf8a;padding:6px 10px;margin:0.5rem 0;background:rgba(22,160,133,.12)">'
                f'<b>Подписка активна:</b> {html_mod.escape(pkg_lbl)} '
                f'на {sub["amount_rub"]}₽/мес · '
                f'списаний: {sub["charges_count"]} · '
                f'следующее: {html_mod.escape(sub.get("next_charge_at") or "—")} · '
                f'с {html_mod.escape(sub.get("created_at") or "—")}'
                f'</div>'
            )

        if not items:
            return sub_html or '<p style="color:#8d86ad">Без покупок</p>'

        rows = ""
        for p in items:
            pkg_lbl = _PACKAGES.get(str(p["package"]), f"package={p['package']}")
            rows += (
                f"<tr>"
                f"<td><b>{html_mod.escape(pkg_lbl)}</b></td>"
                f"<td>{p['count']}</td>"
                f"<td>{p['total_rub']}₽</td>"
                f"<td>{p['last_at']}</td>"
                f"</tr>"
            )
        return sub_html + (
            '<div class="table-wrap"><table>'
            '<tr><th>Продукт</th><th>Кол-во оплат</th><th>Сумма</th><th>Последняя</th></tr>'
            f'{rows}</table></div>'
        )

    def _manual_payments_html(tg_id: int, payments: list) -> str:
        if not payments:
            return '<p style="color:#8d86ad">Пока нет ручных платежей</p>'
        rows = ""
        for p in payments:
            note = html_mod.escape(p["note"]) if p["note"] else "<span style='color:#8d86ad'>—</span>"
            actor = html_mod.escape(p["created_by"] or "—")
            rows += (
                f"<tr><td><b>{p['amount_rub']}₽</b></td>"
                f"<td>{note}</td>"
                f"<td>{p['created_at']}</td>"
                f"<td>{actor}</td>"
                f"<td><form method='post' action='/admin/users/{tg_id}/manual-payment/{p['id']}/delete' "
                f"style='display:inline' onsubmit=\"return confirm('Удалить платёж?')\">"
                f"<button style='background:none;color:#ef6a6a;padding:0;font-size:0.85em;cursor:pointer;border:none'>удалить</button>"
                f"</form></td></tr>"
            )
        return (
            '<div class="table-wrap"><table>'
            '<tr><th>Сумма</th><th>Комментарий</th><th>Дата</th><th>Кем</th><th></th></tr>'
            f'{rows}</table></div>'
        )

    def _parse_dt_local(value: str) -> Optional[datetime]:
        s = str(value or "").strip()
        if not s:
            return None
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    # ── Broadcasts: list ──────────────────────────────────────────────

    @app.get("/admin/broadcasts", response_class=HTMLResponse)
    async def broadcasts_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        page = _query_int(request, "page", default=1, min_value=1, max_value=10_000)
        per_page = 50
        rows = await credits_db.list_broadcasts(limit=per_page, offset=(page - 1) * per_page)
        total = await credits_db.count_broadcasts()
        total_pages = max(1, (total + per_page - 1) // per_page)

        status_badges = {
            "draft": '<span class="badge badge-stage">черновик</span>',
            "scheduled": '<span class="badge badge-source">запланирована</span>',
            "sending": '<span class="badge badge-stage">отправляется</span>',
            "done": '<span class="badge badge-ok">готово</span>',
            "paused": '<span class="badge badge-source">пауза</span>',
            "cancelled": '<span class="badge badge-zero">отменена</span>',
        }
        tr = []
        for r in rows:
            sent = r["sent_count"]
            fail = r["failed_count"]
            size = r["audience_size"]
            progress = f"{sent}/{size}" if size else f"{sent}"
            fail_str = f' <span style="color:#ef6a6a">(–{fail})</span>' if fail else ""
            tr.append(
                f"<tr><td><a href='/admin/broadcasts/{r['id']}'>#{r['id']}</a></td>"
                f"<td>{html_mod.escape(r['title'])}</td>"
                f"<td>{status_badges.get(r['status'], html_mod.escape(r['status']))}</td>"
                f"<td>{progress}{fail_str}</td>"
                f"<td>{html_mod.escape(r['schedule_at'] or '—')}</td>"
                f"<td>{html_mod.escape(r['created_by'] or '—')}</td>"
                f"<td>{r['created_at']}</td></tr>"
            )

        done_total = sum(1 for r in rows if r["status"] == "done")
        in_flight = sum(1 for r in rows if r["status"] in ("scheduled", "sending"))
        body = f"""
        <div class="card hero">
          <a class="btn btn-lg" href="/admin/broadcasts/new">Создать рассылку</a>
          <p class="meta">Сообщение от имени бота · аудитория: вся база, по источнику, по тарифу или вручную · можно запланировать</p>
        </div>

        <div class="card">
          <div class="toolbar">
            <h2 style="margin:0">История</h2>
            <div class="stage-grid" style="margin:0">
              <div class="stage-chip" style="padding:8px 14px"><div class="count" style="font-size:1.3em">{total}</div><div class="label">всего</div></div>
              <div class="stage-chip" style="padding:8px 14px"><div class="count" style="font-size:1.3em">{in_flight}</div><div class="label">в работе</div></div>
              <div class="stage-chip" style="padding:8px 14px"><div class="count" style="font-size:1.3em">{done_total}</div><div class="label">отправлено</div></div>
            </div>
          </div>
          <div class="table-wrap" style="margin-top:12px"><table>
          <tr><th>#</th><th>Название</th><th>Статус</th><th>Доставлено</th><th>Запланирована</th><th>Автор</th><th>Создана</th></tr>
          {''.join(tr) if tr else '<tr><td colspan=7>Пока нет рассылок — первая в одну кнопку выше</td></tr>'}
          </table></div>
          {_pagination_html(page, total_pages, base_url='?')}
        </div>
        """
        return _page("Рассылки", body)

    # ── Broadcasts: create form ───────────────────────────────────────

    @app.get("/admin/broadcasts/new", response_class=HTMLResponse)
    async def broadcasts_new_form(request: Request, _user: str = Depends(_check_auth)) -> str:
        preset_tier = str(request.query_params.get("tier") or "").strip().upper()
        if preset_tier and preset_tier not in _TIER_SPEC:
            preset_tier = ""

        tags = await credits_db.list_all_tags()
        tag_opts = "".join(
            f'<option value="{html_mod.escape(t["tag"])}">{html_mod.escape(t["tag"])} ({t["count"]})</option>'
            for t in tags
        )
        sources_dist = await credits_db.source_distribution()
        src_opts = "".join(
            f'<option value="{html_mod.escape(d["source"])}">{html_mod.escape(d["source"])} ({d["count"]})</option>'
            for d in sources_dist
        )
        tier_counts = await credits_db.tier_counts()
        tier_opts = "".join(
            f'<option value="{code}" {"selected" if code == preset_tier else ""}>'
            f'{code} — {html_mod.escape(spec["title"])} ({tier_counts.get(code, 0)})'
            f'</option>'
            for code, spec in _TIER_SPEC.items()
        )
        mode_default = "tier" if preset_tier else "all"

        body = f"""
        <form method="post" action="/admin/broadcasts/new" id="bc-form" style="display:block">
        <div class="pm-grid">

          <div class="pm-panel" style="grid-column: span 7">
            <div class="ph"><b>Сообщение</b></div>
            <div class="stack">
              <label class="field"><span>Название — только для истории</span><input type="text" name="title" required placeholder="Скидка на Глоу до воскресенья"></label>
              <label class="field"><span>Текст</span><textarea name="text" id="bc-text" required placeholder="Привет! …"></textarea></label>
              <div class="row">
                <label class="field" style="width:160px"><span>Разметка</span>
                  <select name="parse_mode"><option value="HTML" selected>HTML</option><option value="MARKDOWN">Markdown</option><option value="">без разметки</option></select>
                </label>
                <label class="field" style="width:180px"><span>Медиа</span>
                  <select name="media_type" id="bc-media-type"><option value="">без медиа</option><option value="photo">фото</option><option value="video">видео</option><option value="animation">gif</option><option value="document">файл</option></select>
                </label>
              </div>
              <div class="row" id="bc-media-fields" hidden>
                <label class="field" style="flex:1;min-width:220px"><span>Ссылка на файл</span><input type="text" name="media_url" placeholder="https://…"></label>
                <span class="meta">или</span>
                <label class="field" style="flex:1;min-width:220px"><span>Telegram file_id</span><input type="text" name="media_file_id" placeholder="AgACAg…"></label>
                <p class="meta" style="width:100%;margin:0">file_id: отправь медиа боту с подписью <code>/fileid</code> — он пришлёт id.</p>
              </div>
              <label class="field"><span>Кнопки — по одной на строку: <code>Текст | https://ссылка</code></span>
                <textarea name="buttons_raw" id="bc-buttons" style="min-height:64px;font-family:ui-monospace,monospace" placeholder="Открыть бота | https://t.me/{html_mod.escape(settings.tg_bot_username or 'bot')}"></textarea>
              </label>
            </div>
          </div>

          <div class="pm-panel" style="grid-column: span 5; gap: 10px">
            <div class="ph"><b>Как увидит клиент</b></div>
            <div class="tg-preview sticky">
              <div class="who">@{html_mod.escape(settings.tg_bot_username or 'bot')}</div>
              <div class="tg-bubble" id="bc-bubble"><div class="media" id="bc-media" hidden>медиа</div><span id="bc-preview-text" style="color:#8aa0b5">Текст появится здесь</span></div>
              <div class="tg-buttons" id="bc-preview-buttons"></div>
            </div>
          </div>

          <div class="pm-panel" style="grid-column: span 12">
            <div class="ph"><b>Кому</b><label class="row" style="font-size:.9em;color:var(--text-70)"><input type="checkbox" name="exclude_blocked" checked> не слать тем, кто заблокировал бота</label></div>
            <div class="choice-grid" id="bc-modes">
              <label class="choice"><input type="radio" name="mode" value="all" {"checked" if mode_default == "all" else ""}><b>Вся база</b><small>все, кто запускал бота</small></label>
              <label class="choice"><input type="radio" name="mode" value="tier" {"checked" if mode_default == "tier" else ""}><b>По тарифу</b><small>сегменты из раздела «Тарифы»</small></label>
              <label class="choice"><input type="radio" name="mode" value="source"><b>По источнику</b><small>start-параметр deep-link</small></label>
              <label class="choice"><input type="radio" name="mode" value="filter"><b>По условиям</b><small>баланс, оплаты, теги, дата</small></label>
              <label class="choice"><input type="radio" name="mode" value="manual"><b>Точечно</b><small>список @username или id</small></label>
              <div class="choice-body" data-mode="tier">
                <label class="field" style="max-width:420px"><span>Тариф</span><select name="tier_value"><option value="">— выбрать —</option>{tier_opts}</select></label>
              </div>
              <div class="choice-body" data-mode="source">
                <label class="field" style="max-width:320px"><span>Источник</span><select name="source_value"><option value="">— любой —</option>{src_opts}</select></label>
              </div>
              <div class="choice-body" data-mode="filter">
                <div class="row">
                  <label class="field" style="width:120px"><span>Баланс от</span><input type="number" name="credits_min"></label>
                  <label class="field" style="width:120px"><span>до</span><input type="number" name="credits_max"></label>
                  <label class="field" style="width:130px"><span>Платил</span><select name="paid"><option value="any">неважно</option><option value="yes">да</option><option value="no">нет</option></select></label>
                  <label class="field" style="width:130px"><span>Генерил</span><select name="generated"><option value="any">неважно</option><option value="yes">да</option><option value="no">нет</option></select></label>
                  <label class="field" style="width:180px"><span>Тег</span><select name="tag"><option value="">любой</option>{tag_opts}</select></label>
                  <label class="field" style="width:160px"><span>Пришёл с</span><input type="date" name="created_from"></label>
                  <label class="field" style="width:160px"><span>по</span><input type="date" name="created_to"></label>
                </div>
              </div>
              <div class="choice-body" data-mode="manual">
                <label class="field"><span>Через запятую</span><input type="text" name="manual_raw" placeholder="@username, 123456789, …"></label>
              </div>
            </div>
          </div>

          <div class="pm-panel" style="grid-column: span 12">
            <div class="ph"><b>Когда</b></div>
            <div class="choice-grid" style="grid-template-columns: repeat(auto-fit, minmax(220px, 1fr))">
              <label class="choice"><input type="radio" name="when" value="draft" checked><b>Сохранить черновик</b><small>отправить позже из истории</small></label>
              <label class="choice"><input type="radio" name="when" value="now"><b>Отправить сейчас</b><small>уйдёт сразу после создания</small></label>
              <label class="choice"><input type="radio" name="when" value="schedule"><b>Запланировать</b><small>время по UTC</small><input type="datetime-local" name="schedule_at" style="margin-top:6px;position:static;opacity:1;width:auto" onclick="this.closest('.choice').querySelector('input[type=radio]').checked=true"></label>
            </div>
            <div class="row" style="justify-content:flex-end;margin-top:6px">
              <a href="/admin/broadcasts" class="btn btn-secondary">Отмена</a>
              <button type="submit" class="btn-lg">Создать рассылку</button>
            </div>
          </div>
        </div>
        </form>
        <script>
        (function() {{
          const text = document.getElementById('bc-text'), out = document.getElementById('bc-preview-text');
          const btns = document.getElementById('bc-buttons'), outBtns = document.getElementById('bc-preview-buttons');
          const mt = document.getElementById('bc-media-type'), mf = document.getElementById('bc-media-fields'), mp = document.getElementById('bc-media');
          function esc(t) {{ return t.replace(/[&<>]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}})[c]); }}
          function render() {{
            const v = text.value.trim();
            out.style.color = v ? '' : '#8aa0b5';
            let h = esc(v);
            for (const tag of ['b', 'i', 'u', 's', 'code']) {{
              h = h.split('&lt;' + tag + '&gt;').join('<' + tag + '>').split('&lt;/' + tag + '&gt;').join('</' + tag + '>');
            }}
            out.innerHTML = v ? h : 'Текст появится здесь';
            outBtns.innerHTML = btns.value.split(String.fromCharCode(10)).map(l => l.split('|')[0].trim()).filter(Boolean).map(t => '<span>' + esc(t) + '</span>').join('');
            const m = mt.value; mf.hidden = !m; mp.hidden = !m; if (m) mp.textContent = ({{photo:'фото', video:'видео', animation:'gif', document:'файл'}})[m] || m;
          }}
          [text, btns, mt].forEach(el => el.addEventListener('input', render)); render();
          const modes = document.getElementById('bc-modes');
          function syncModes() {{
            const cur = (modes.querySelector('input[name=mode]:checked') || {{}}).value;
            modes.querySelectorAll('.choice-body').forEach(b => b.classList.toggle('open', b.dataset.mode === cur));
          }}
          modes.addEventListener('change', syncModes); syncModes();
        }})();
        </script>
        """
        return _page("Новая рассылка", body, back=("Рассылки", "/admin/broadcasts"))

    @app.post("/admin/broadcasts/new")
    async def broadcasts_new_submit(
        title: str = Form(...),
        text: str = Form(""),
        parse_mode: str = Form("HTML"),
        media_type: str = Form(""),
        media_url: str = Form(""),
        media_file_id: str = Form(""),
        buttons_raw: str = Form(""),
        mode: str = Form("all"),
        source_value: str = Form(""),
        tier_value: str = Form(""),
        credits_min: str = Form(""),
        credits_max: str = Form(""),
        paid: str = Form("any"),
        generated: str = Form("any"),
        tag: str = Form(""),
        created_from: str = Form(""),
        created_to: str = Form(""),
        manual_raw: str = Form(""),
        exclude_blocked: str = Form(""),
        when: str = Form("draft"),
        schedule_at: str = Form(""),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        audience: dict = {"mode": mode, "exclude_blocked": bool(exclude_blocked)}
        if mode == "source":
            audience["source"] = {"value": source_value.strip()}
        elif mode == "tier":
            audience["tier"] = {"value": tier_value.strip().upper()}
        elif mode == "filter":
            audience["filter"] = {
                "credits_min": credits_min.strip() or None,
                "credits_max": credits_max.strip() or None,
                "paid": paid, "generated": generated,
                "tag": tag.strip(),
                "created_from": created_from.strip() or None,
                "created_to": created_to.strip() or None,
            }
        elif mode == "manual":
            audience["manual"] = _parse_manual_list(manual_raw)

        buttons = _parse_buttons(buttons_raw)
        sched_dt = _parse_dt_local(schedule_at) if when == "schedule" else None

        bid = await credits_db.create_broadcast(
            title=title, text=text, parse_mode=parse_mode,
            media_type=media_type, media_file_id=media_file_id, media_url=media_url,
            buttons=buttons, audience=audience, schedule_at=sched_dt,
            created_by=_user,
        )
        await credits_db.audit_log(_user, "broadcast_create", str(bid), _aud_summary(audience))

        if when == "draft":
            # Pre-resolve audience so the draft preview shows real counts instead of 0.
            ids = await credits_db.resolve_audience(audience)
            await credits_db.set_broadcast_status(bid, "draft", audience_size=len(ids))
        if when == "now":
            # Resolve audience, seed, mark sending — worker picks up.
            ids = await credits_db.resolve_audience(audience)
            await credits_db.seed_broadcast_deliveries(bid, ids)
            await credits_db.set_broadcast_status(
                bid, "sending", started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                audience_size=len(ids),
            )
            await credits_db.audit_log(_user, "broadcast_start", str(bid), f"audience={len(ids)}")
        elif when == "schedule" and sched_dt is not None:
            ids = await credits_db.resolve_audience(audience)
            await credits_db.set_broadcast_status(bid, "scheduled", audience_size=len(ids))
            await credits_db.audit_log(_user, "broadcast_schedule", str(bid), f"at={sched_dt} audience={len(ids)}")
        return RedirectResponse(f"/admin/broadcasts/{bid}", status_code=303)

    # ── Broadcasts: edit (drafts only) ────────────────────────────────

    @app.get("/admin/broadcasts/{bid}/edit", response_class=HTMLResponse)
    async def broadcasts_edit_form(bid: int, _user: str = Depends(_check_auth)) -> str:
        bc = await credits_db.get_broadcast(bid)
        if not bc:
            raise HTTPException(404, "Broadcast not found")
        if bc["status"] != "draft":
            raise HTTPException(400, "Можно редактировать только черновики")

        aud = bc["audience"] or {}
        cur_mode = str(aud.get("mode") or "all").lower()
        cur_source = str((aud.get("source") or {}).get("value") or "")
        cur_tier = str((aud.get("tier") or {}).get("value") or "").upper()
        f = aud.get("filter") or {}
        m = aud.get("manual") or {}
        cur_manual = ""
        if m:
            parts: list[str] = []
            parts.extend(str(x) for x in (m.get("tg_ids") or []))
            parts.extend(f"@{u}" for u in (m.get("usernames") or []))
            cur_manual = ", ".join(parts)
        cur_exclude_blocked = bool(aud.get("exclude_blocked", True))

        tags = await credits_db.list_all_tags()
        cur_tag = str(f.get("tag") or "")
        tag_opts = '<option value="">—</option>' + "".join(
            f'<option value="{html_mod.escape(t["tag"])}" {"selected" if t["tag"] == cur_tag else ""}>'
            f'{html_mod.escape(t["tag"])} ({t["count"]})</option>'
            for t in tags
        )
        sources_dist = await credits_db.source_distribution()
        src_opts = '<option value="">— любой —</option>' + "".join(
            f'<option value="{html_mod.escape(d["source"])}" {"selected" if d["source"] == cur_source else ""}>'
            f'{html_mod.escape(d["source"])} ({d["count"]})</option>'
            for d in sources_dist
        )
        tier_counts = await credits_db.tier_counts()
        tier_opts = '<option value="">— выбрать тир —</option>' + "".join(
            f'<option value="{code}" {"selected" if code == cur_tier else ""}>'
            f'{code} — {html_mod.escape(spec["title"])} ({tier_counts.get(code, 0)})'
            f'</option>'
            for code, spec in _TIER_SPEC.items()
        )

        def _chk(mode: str) -> str:
            return "checked" if cur_mode == mode else ""

        def _pm_sel(v: str) -> str:
            cur = (bc["parse_mode"] or "HTML").upper()
            return "selected" if v.upper() == cur else ""

        def _mt_sel(v: str) -> str:
            return "selected" if v == bc["media_type"] else ""

        buttons_raw = "\n".join(
            f"{b.get('text','')} | {b.get('url','')}" for b in (bc["buttons"] or [])
        )

        sched_val = ""
        if bc.get("_schedule_raw"):
            try:
                sched_val = bc["_schedule_raw"].strftime("%Y-%m-%dT%H:%M")
            except Exception:
                sched_val = ""

        body = f"""
        <div class="card">
        <form method="post" action="/admin/broadcasts/{bid}/edit">
          <h3>1. Сообщение</h3>
          <label>Название: <input type="text" name="title" value="{html_mod.escape(bc['title'], quote=True)}" required style="width:400px"></label><br><br>
          <label>Текст (HTML разрешён):<br>
            <textarea name="text" rows="6" style="width:100%;font-family:inherit" required>{html_mod.escape(bc['text'])}</textarea>
          </label><br>
          <label>Parse mode:
            <select name="parse_mode">
              <option value="HTML" {_pm_sel("HTML")}>HTML</option>
              <option value="MARKDOWN" {_pm_sel("MARKDOWN")}>Markdown</option>
              <option value="" {_pm_sel("")}>plain</option>
            </select>
          </label><br><br>

          <h3>2. Медиа</h3>
          <label>Тип:
            <select name="media_type">
              <option value="" {_mt_sel("")}>без медиа</option>
              <option value="photo" {_mt_sel("photo")}>photo</option>
              <option value="video" {_mt_sel("video")}>video</option>
              <option value="animation" {_mt_sel("animation")}>animation (gif)</option>
              <option value="document" {_mt_sel("document")}>document</option>
            </select>
          </label>
          <label>URL: <input type="text" name="media_url" value="{html_mod.escape(bc['media_url'], quote=True)}" style="width:380px"></label><br>
          <label>или file_id: <input type="text" name="media_file_id" value="{html_mod.escape(bc['media_file_id'], quote=True)}" style="width:420px"></label><br><br>

          <h3>3. Кнопки</h3>
          <p style="color:#c6bfe4;font-size:0.85em">По одной на строку: <code>Текст | https://url</code></p>
          <textarea name="buttons_raw" rows="3" style="width:100%;font-family:monospace">{html_mod.escape(buttons_raw)}</textarea><br><br>

          <h3>4. Аудитория</h3>
          <label><input type="radio" name="mode" value="all" {_chk("all")}> Вся база</label><br>
          <label><input type="radio" name="mode" value="tier" {_chk("tier")}> По тиру</label>
          <span style="margin-left:1em">
            <select name="tier_value" style="width:380px">{tier_opts}</select>
          </span><br>
          <label><input type="radio" name="mode" value="source" {_chk("source")}> По источнику</label>
          <span style="margin-left:1em">
            source: <select name="source_value" style="width:240px">{src_opts}</select>
          </span><br>
          <label><input type="radio" name="mode" value="filter" {_chk("filter")}> Фильтр по базе</label>
          <span style="margin-left:1em">
            credits≥ <input type="number" name="credits_min" value="{html_mod.escape(str(f.get('credits_min') or ''), quote=True)}" style="width:60px">
            credits≤ <input type="number" name="credits_max" value="{html_mod.escape(str(f.get('credits_max') or ''), quote=True)}" style="width:60px">
            платил:
            <select name="paid">
              <option value="any" {"selected" if (f.get("paid") or "any") == "any" else ""}>—</option>
              <option value="yes" {"selected" if f.get("paid") == "yes" else ""}>да</option>
              <option value="no" {"selected" if f.get("paid") == "no" else ""}>нет</option>
            </select>
            генерил:
            <select name="generated">
              <option value="any" {"selected" if (f.get("generated") or "any") == "any" else ""}>—</option>
              <option value="yes" {"selected" if f.get("generated") == "yes" else ""}>да</option>
              <option value="no" {"selected" if f.get("generated") == "no" else ""}>нет</option>
            </select>
            tag: <select name="tag">{tag_opts}</select>
            с: <input type="date" name="created_from" value="{html_mod.escape(str(f.get('created_from') or ''), quote=True)}">
            до: <input type="date" name="created_to" value="{html_mod.escape(str(f.get('created_to') or ''), quote=True)}">
          </span><br>
          <label><input type="radio" name="mode" value="manual" {_chk("manual")}> Точечно</label>
          <span style="margin-left:1em">
            <input type="text" name="manual_raw" value="{html_mod.escape(cur_manual, quote=True)}" placeholder="@username, 123456789, ..." style="width:560px">
          </span><br><br>
          <label><input type="checkbox" name="exclude_blocked" {"checked" if cur_exclude_blocked else ""}> Исключить тех, кто блокнул бота</label><br><br>

          <h3>5. Расписание</h3>
          <label><input type="radio" name="when" value="draft" checked> Оставить черновиком</label><br>
          <label><input type="radio" name="when" value="schedule"> Запланировать на:
            <input type="datetime-local" name="schedule_at" value="{sched_val}"> <small>(UTC)</small>
          </label><br>
          <label><input type="radio" name="when" value="clear"> Снять расписание</label><br><br>

          <button type="submit" class="btn-success">Сохранить</button>
          <a href="/admin/broadcasts/{bid}" style="margin-left:1rem">Отмена</a>
        </form>
        </div>
        """
        return _page(f"Изменить рассылку #{bid}", body, back=[("Рассылки", "/admin/broadcasts"), (f"Рассылка #{bid}", f"/admin/broadcasts/{bid}")])

    @app.post("/admin/broadcasts/{bid}/edit")
    async def broadcasts_edit_submit(
        bid: int,
        title: str = Form(...),
        text: str = Form(""),
        parse_mode: str = Form("HTML"),
        media_type: str = Form(""),
        media_url: str = Form(""),
        media_file_id: str = Form(""),
        buttons_raw: str = Form(""),
        mode: str = Form("all"),
        source_value: str = Form(""),
        tier_value: str = Form(""),
        credits_min: str = Form(""),
        credits_max: str = Form(""),
        paid: str = Form("any"),
        generated: str = Form("any"),
        tag: str = Form(""),
        created_from: str = Form(""),
        created_to: str = Form(""),
        manual_raw: str = Form(""),
        exclude_blocked: str = Form(""),
        when: str = Form("draft"),
        schedule_at: str = Form(""),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        bc = await credits_db.get_broadcast(bid)
        if not bc:
            raise HTTPException(404, "Broadcast not found")
        if bc["status"] != "draft":
            raise HTTPException(400, "Можно редактировать только черновики")

        audience: dict = {"mode": mode, "exclude_blocked": bool(exclude_blocked)}
        if mode == "source":
            audience["source"] = {"value": source_value.strip()}
        elif mode == "tier":
            audience["tier"] = {"value": tier_value.strip().upper()}
        elif mode == "filter":
            audience["filter"] = {
                "credits_min": credits_min.strip() or None,
                "credits_max": credits_max.strip() or None,
                "paid": paid, "generated": generated,
                "tag": tag.strip(),
                "created_from": created_from.strip() or None,
                "created_to": created_to.strip() or None,
            }
        elif mode == "manual":
            audience["manual"] = _parse_manual_list(manual_raw)

        buttons = _parse_buttons(buttons_raw)

        kwargs: dict = dict(
            title=title, text=text, parse_mode=parse_mode,
            media_type=media_type, media_file_id=media_file_id, media_url=media_url,
            buttons=buttons, audience=audience,
        )
        if when == "schedule":
            sched_dt = _parse_dt_local(schedule_at)
            if sched_dt is not None:
                kwargs["schedule_at"] = sched_dt
            else:
                kwargs["clear_schedule"] = True
        elif when == "clear":
            kwargs["clear_schedule"] = True

        await credits_db.update_broadcast(bid, **kwargs)
        ids = await credits_db.resolve_audience(audience)
        await credits_db.set_broadcast_status(bid, "draft", audience_size=len(ids))
        await credits_db.audit_log(_user, "broadcast_edit", str(bid), _aud_summary(audience))
        return RedirectResponse(f"/admin/broadcasts/{bid}", status_code=303)

    # ── Broadcasts: detail ────────────────────────────────────────────

    @app.get("/admin/broadcasts/{bid}", response_class=HTMLResponse)
    async def broadcasts_detail(bid: int, _user: str = Depends(_check_auth)) -> str:
        bc = await credits_db.get_broadcast(bid)
        if not bc:
            raise HTTPException(404, "Broadcast not found")

        deliveries_sent = await credits_db.get_broadcast_deliveries(bid, status="sent", limit=20)
        deliveries_fail = await credits_db.get_broadcast_deliveries(bid, status="failed", limit=20)
        deliveries_blk = await credits_db.get_broadcast_deliveries(bid, status="blocked", limit=20)

        buttons_html = "".join(
            f'<a class="btn" href="{html_mod.escape(b.get("url",""), quote=True)}" target="_blank">{html_mod.escape(b.get("text",""))}</a> '
            for b in (bc["buttons"] or [])
        )
        media_html = ""
        if bc["media_type"]:
            src = bc["media_url"] or bc["media_file_id"]
            media_html = f"<p><b>Медиа:</b> {html_mod.escape(bc['media_type'])} — <code>{html_mod.escape(src)}</code></p>"

        action_buttons = []
        if bc["status"] == "draft":
            action_buttons.append(
                f'<form method="post" action="/admin/broadcasts/{bid}/send" '
                f'onsubmit="return confirm(\'Запустить рассылку сейчас?\')">'
                f'<button class="btn-success">Запустить сейчас</button></form>'
            )
            action_buttons.append(
                f'<form method="post" action="/admin/broadcasts/{bid}/delete" '
                f'onsubmit="return confirm(\'Удалить черновик?\')">'
                f'<button class="btn-danger">Удалить</button></form>'
            )
            action_buttons.append(
                f'<a class="btn" href="/admin/broadcasts/{bid}/edit">Изменить</a>'
            )
        if bc["status"] == "sending":
            action_buttons.append(
                f'<form method="post" action="/admin/broadcasts/{bid}/pause"><button>Пауза</button></form>'
            )
            action_buttons.append(
                f'<form method="post" action="/admin/broadcasts/{bid}/cancel" '
                f'onsubmit="return confirm(\'Отменить рассылку? Недоставленные будут пропущены\')">'
                f'<button class="btn-danger">Отменить</button></form>'
            )
        if bc["status"] == "paused":
            action_buttons.append(
                f'<form method="post" action="/admin/broadcasts/{bid}/send"><button class="btn-success">Продолжить</button></form>'
            )
            action_buttons.append(
                f'<form method="post" action="/admin/broadcasts/{bid}/cancel"><button class="btn-danger">Отменить</button></form>'
            )
        if bc["status"] == "scheduled":
            action_buttons.append(
                f'<form method="post" action="/admin/broadcasts/{bid}/cancel"><button class="btn-danger">Отменить</button></form>'
            )
        if bc["status"] in ("done", "cancelled"):
            action_buttons.append(
                f'<form method="post" action="/admin/broadcasts/{bid}/delete" '
                f'onsubmit="return confirm(\'Удалить рассылку и её delivery-лог?\')">'
                f'<button class="btn-danger">Удалить</button></form>'
            )
        action_buttons.append(
            f'<form method="post" action="/admin/broadcasts/{bid}/test">'
            f'<input type="text" name="target" placeholder="tg_id или @username" style="width:220px">'
            f'<button>Тест</button></form>'
        )

        def _dtable(label: str, rows: list, color: str) -> str:
            if not rows:
                return f"<h4 style='color:{color}'>{label}: 0</h4>"
            trs = "".join(
                f"<tr><td><a href='/admin/users/{r['tg_id']}'>{('@' + r['username']) if r['username'] else r['tg_id']}</a></td>"
                f"<td>{html_mod.escape(r['error'] or '—')}</td><td>{r['sent_at'] or '—'}</td></tr>"
                for r in rows
            )
            return (
                f"<h4 style='color:{color}'>{label}: {len(rows)}</h4>"
                f"<table><tr><th>User</th><th>Error</th><th>At</th></tr>{trs}</table>"
            )

        body = f"""
        <div class="card">
          <h2>#{bc['id']} — {html_mod.escape(bc['title'])}</h2>
          <p>Статус: <b>{html_mod.escape(bc['status'])}</b> |
             Создана: {bc['created_at']} ({html_mod.escape(bc['created_by'] or '—')}) |
             Запланирована: {html_mod.escape(bc['schedule_at'] or '—')}</p>
          <p>Аудитория: {html_mod.escape(_aud_summary(bc['audience']))} ({bc['audience_size']} чел.)</p>
          <p>Отправлено: <b>{bc['sent_count']}</b> · Ошибок: {bc['failed_count']} · Заблокировали: {bc['blocked_count']}</p>
          <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-top:0.5rem">{' '.join(action_buttons)}</div>
        </div>

        <div class="card">
          <h3>Превью</h3>
          {media_html}
          <pre style="white-space:pre-wrap;background:#1a1331;padding:1rem;border-radius:6px">{html_mod.escape(bc['text'])}</pre>
          <div>{buttons_html}</div>
        </div>

        <div class="card">
          {_dtable('Доставлено', deliveries_sent, '#4ecf8a')}
          {_dtable('Ошибки', deliveries_fail, '#ef6a6a')}
          {_dtable('Заблокировали', deliveries_blk, '#8d86ad')}
        </div>
        """
        return _page(f"Рассылка #{bc['id']}", body, back=("Рассылки", "/admin/broadcasts"))

    @app.post("/admin/broadcasts/{bid}/send")
    async def broadcasts_send(bid: int, _user: str = Depends(_check_auth)) -> RedirectResponse:
        bc = await credits_db.get_broadcast(bid)
        if not bc:
            raise HTTPException(404)
        # If switching from draft/paused/scheduled → sending
        if bc["status"] == "draft":
            ids = await credits_db.resolve_audience(bc["audience"])
            await credits_db.seed_broadcast_deliveries(bid, ids)
            await credits_db.set_broadcast_status(
                bid, "sending", started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                audience_size=len(ids),
            )
        else:
            await credits_db.set_broadcast_status(bid, "sending")
        await credits_db.audit_log(_user, "broadcast_start", str(bid))
        return RedirectResponse(f"/admin/broadcasts/{bid}", status_code=303)

    @app.post("/admin/broadcasts/{bid}/pause")
    async def broadcasts_pause(bid: int, _user: str = Depends(_check_auth)) -> RedirectResponse:
        await credits_db.set_broadcast_status(bid, "paused")
        await credits_db.audit_log(_user, "broadcast_pause", str(bid))
        return RedirectResponse(f"/admin/broadcasts/{bid}", status_code=303)

    @app.post("/admin/broadcasts/{bid}/cancel")
    async def broadcasts_cancel(bid: int, _user: str = Depends(_check_auth)) -> RedirectResponse:
        await credits_db.set_broadcast_status(
            bid, "cancelled", finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        await credits_db.audit_log(_user, "broadcast_cancel", str(bid))
        return RedirectResponse(f"/admin/broadcasts/{bid}", status_code=303)

    @app.post("/admin/broadcasts/{bid}/delete")
    async def broadcasts_delete(bid: int, _user: str = Depends(_check_auth)) -> RedirectResponse:
        await credits_db.delete_broadcast(bid)
        await credits_db.audit_log(_user, "broadcast_delete", str(bid))
        return RedirectResponse("/admin/broadcasts", status_code=303)

    @app.post("/admin/broadcasts/{bid}/test")
    async def broadcasts_test(
        bid: int, target: str = Form(""), _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        bc = await credits_db.get_broadcast(bid)
        if not bc:
            raise HTTPException(404)
        if not bot_ref or not bot_ref[0]:
            raise HTTPException(503, "Bot not ready")
        tg_id = 0
        tgt = str(target or "").strip().lstrip("@")
        if tgt.lstrip("-").isdigit():
            tg_id = int(tgt)
        elif tgt:
            found = await credits_db.search_users(tgt, limit=1)
            if found:
                tg_id = int(found[0]["tg_id"])
        if tg_id <= 0:
            tg_id = int(getattr(settings, "manager_chat_id", 0) or 0)
        if tg_id <= 0:
            raise HTTPException(400, "Укажи tg_id/@username для теста")
        try:
            await send_bot_message(
                bot_ref[0], tg_id,
                text=bc["text"], parse_mode=bc["parse_mode"],
                media_type=bc["media_type"], media_file_id=bc["media_file_id"], media_url=bc["media_url"],
                buttons=bc["buttons"],
            )
            await credits_db.audit_log(_user, "broadcast_test", str(bid), f"to={tg_id}")
        except Exception as e:
            await credits_db.audit_log(_user, "broadcast_test_fail", str(bid), f"to={tg_id} err={e}")
            raise HTTPException(500, f"Test send failed: {e}")
        return RedirectResponse(f"/admin/broadcasts/{bid}", status_code=303)

    @app.post("/admin/broadcasts/preview")
    async def broadcasts_audience_preview(request: Request, _user: str = Depends(_check_auth)) -> JSONResponse:
        try:
            data = await request.json()
        except Exception:
            data = {}
        ids = await credits_db.resolve_audience(data or {"mode": "all"})
        return JSONResponse({"count": len(ids)})

    # ── Clients (CRM tab) ─────────────────────────────────────────────

    _PRODUCT_LABELS = (
        ("", "— все продукты —"),
        ("any", "хотя бы одна покупка"),
        ("none", "без покупок"),
        ("trial", "Триал (5)"),
        ("blast", "Бласт (15) — разовая"),
        ("blast_subscription", "Бласт — подписка"),
        ("glow", "Глоу (30)"),
        ("impulse", "Импульс (50)"),
    )

    def _bought_packages_html(bought: list, has_sub: bool) -> str:
        labels = []
        # payments.package may be either a numeric code or a Russian name.
        codes_bought: set[str] = set()
        for p in (bought or []):
            code = _normalize_pkg_code(str(p))
            if code:
                codes_bought.add(code)
        # An active subscription is always shown as "Подписка" (teal). Its
        # underlying payments row with package='15'/'Бласт' would otherwise
        # also render as one-off "Бласт" — drop it to avoid the duplicate.
        if has_sub:
            labels.append('<span class="badge badge-ok">Подписка</span>')
            codes_bought.discard("15")
        pkg_styles = {
            "5":  ('Триал', '#5aa9ea'),
            "15": ('Бласт', '#4ecf8a'),
            "30": ('Глоу', '#f0b04a'),
            "50": ('Импульс', '#ef6a6a'),
        }
        for code in ("5", "15", "30", "50"):
            if code in codes_bought:
                lbl, color = pkg_styles[code]
                labels.append(f'<span class="badge" style="background:color-mix(in srgb, {color} 18%, transparent);color:{color}">{lbl}</span>')
        return " ".join(labels) if labels else '<span style="color:#8d86ad">—</span>'

    @app.get("/admin/clients", response_class=HTMLResponse)
    async def clients_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        page = _query_int(request, "page", default=1, min_value=1, max_value=10_000)
        min_c = _query_int(request, "min_credits", default=0, min_value=0, max_value=10_000)
        tag_filter = str(request.query_params.get("tag") or "").strip()
        product_filter = str(request.query_params.get("product") or "").strip().lower()
        sort = str(request.query_params.get("sort") or "credits")
        per_page = 50

        # When filtering by product, the user may have burned through their
        # credits — clamp the balance threshold so they still show up.
        effective_min_c = 0 if product_filter else min_c

        summary = await credits_db.clients_summary(min_credits=effective_min_c)
        rows = await credits_db.list_clients(
            min_credits=effective_min_c, limit=per_page, offset=(page - 1) * per_page,
            tag=tag_filter, sort=sort, product=product_filter,
        )
        total = await credits_db.count_clients(
            min_credits=effective_min_c, tag=tag_filter, product=product_filter,
        )
        total_pages = max(1, (total + per_page - 1) // per_page)
        tags_map = await credits_db.get_tags_for_users([r["tg_id"] for r in rows])
        all_tags = await credits_db.list_all_tags()

        # Pull next_charge_at for displayed users' active subscriptions.
        next_charge_map: Dict[int, str] = {}
        if rows:
            ids = [r["tg_id"] for r in rows]
            try:
                pool = credits_db._pool_or_fail()
                async with pool.acquire() as conn:
                    nc_rows = await conn.fetch(
                        "SELECT tg_id, next_charge_at FROM subscriptions "
                        "WHERE status = 'active' AND tg_id = ANY($1::BIGINT[])",
                        ids,
                    )
                for nr in nc_rows:
                    nca = nr["next_charge_at"]
                    next_charge_map[int(nr["tg_id"])] = (
                        nca.strftime("%Y-%m-%d") if nca else ""
                    )
            except Exception as e:
                log.warning("clients next_charge lookup failed: %s", e)

        tag_opts = '<option value="">— все теги —</option>' + "".join(
            f'<option value="{html_mod.escape(t["tag"])}" '
            f'{"selected" if t["tag"] == tag_filter else ""}>{html_mod.escape(t["tag"])} ({t["count"]})</option>'
            for t in all_tags
        )
        sort_opts = "".join(
            f'<option value="{v}" {"selected" if v == sort else ""}>{lbl}</option>'
            for v, lbl in (("credits", "по балансу"), ("recent", "по активности"), ("oldest", "старые"))
        )
        product_opts = "".join(
            f'<option value="{v}" {"selected" if v == product_filter else ""}>{lbl}</option>'
            for v, lbl in _PRODUCT_LABELS
        )

        tr = []
        for r in rows:
            uname = f"@{r['username']}" if r['username'] else str(r['tg_id'])
            products_cell = _bought_packages_html(r.get("bought_packages") or [], r.get("has_active_subscription"))
            next_charge = next_charge_map.get(int(r["tg_id"]), "")
            next_charge_cell = (
                f'<span style="color:#4ecf8a">{html_mod.escape(next_charge)}</span>'
                if next_charge else '<span style="color:#8d86ad">—</span>'
            )
            tr.append(
                f"<tr><td><a href='/admin/users/{r['tg_id']}'>{html_mod.escape(uname)}</a></td>"
                f"<td><b>{r['credits']}</b></td>"
                f"<td>{r['gens_done']}</td>"
                f"<td>{r['revenue_rub']}₽</td>"
                f"<td>{products_cell}</td>"
                f"<td>{next_charge_cell}</td>"
                f"<td>{html_mod.escape(r['last_activity_at'] or '—')}</td>"
                f"<td>{html_mod.escape(r['source'] or '(direct)')}</td></tr>"
            )

        export_qs = (
            f"min_credits={min_c}&tag={url_quote(tag_filter)}"
            f"&product={url_quote(product_filter)}"
        )
        body = f"""
        <div class="card">
          <h2>Сводка</h2>
          <div class="stage-grid">
            <div class="stage-chip"><div class="count">{summary['clients_count']}</div><div class="label">клиентов</div></div>
            <div class="stage-chip"><div class="count">{summary['credits_on_balance']}</div><div class="label">кредитов на балансе</div></div>
            <div class="stage-chip"><div class="count" style="color:var(--ok)">{summary['active_7d']}</div><div class="label">активны за 7 дней</div></div>
            <div class="stage-chip"><div class="count" style="color:{'var(--warn)' if summary['dormant_14d'] else 'var(--text)'}">{summary['dormant_14d']}</div><div class="label">спят 14+ дней</div></div>
            <div class="stage-chip"><div class="count">{summary['revenue_rub_total']:,}&nbsp;&#8381;</div><div class="label">выручка с клиентов</div></div>
          </div>
        </div>

        <div class="card">
          <form method="get" class="filters" style="display:flex">
            <label class="f">Баланс от<input type="number" name="min_credits" value="{min_c}" min="0" style="width:110px;min-width:0"></label>
            <label class="f">Тег<select name="tag">{tag_opts}</select></label>
            <label class="f">Продукт<select name="product">{product_opts}</select></label>
            <label class="f">Сортировка<select name="sort">{sort_opts}</select></label>
            <button type="submit">Показать</button>
            <span class="spacer"></span>
            <a href="/admin/clients/export?{export_qs}" class="btn btn-secondary">Экспорт CSV</a>
          </form>
        </div>

        <div class="card">
          <div class="toolbar"><h2 style="margin:0">Клиенты</h2><span class="meta">{total} · только с оплатами (бот или вручную)</span></div>
          <div class="table-wrap" style="margin-top:8px"><table>
            <tr><th>Клиент</th><th>Баланс</th><th>Генераций</th><th>Выручка</th><th>Продукты</th>
                <th>Списание</th><th>Активность</th><th>Источник</th></tr>
            {''.join(tr) if tr else '<tr><td colspan=8>Никого не нашли — в клиентах только те, кто платил (через бота или вручную).</td></tr>'}
          </table></div>
          {_pagination_html(page, total_pages, base_url=f'?{export_qs}&sort={sort}&')}
        </div>
        """
        return _page("Клиенты", body)

    @app.get("/admin/clients/export", response_class=PlainTextResponse)
    async def clients_export(request: Request, _user: str = Depends(_check_auth)) -> PlainTextResponse:
        min_c = _query_int(request, "min_credits", default=5, min_value=1, max_value=10_000)
        tag = str(request.query_params.get("tag") or "").strip()
        product = str(request.query_params.get("product") or "").strip().lower()
        rows = await credits_db.list_clients(
            min_credits=min_c, limit=10_000, offset=0, tag=tag, sort="credits", product=product,
        )
        lines = [
            "tg_id,username,credits,gens_done,revenue_rub,products,subscription,"
            "last_activity,source,created_at"
        ]
        for r in rows:
            def esc(v: Any) -> str:
                s = str(v or "").replace('"', '""')
                return f'"{s}"' if ("," in s or '"' in s) else s
            code_to_slug = {"5": "trial", "15": "blast", "30": "glow", "50": "impulse"}
            products_str = "/".join(
                code_to_slug.get(_normalize_pkg_code(str(p)), str(p))
                for p in (r.get("bought_packages") or [])
            )
            sub_str = "yes" if r.get("has_active_subscription") else ""
            lines.append(",".join([
                str(r["tg_id"]), esc(r["username"]), str(r["credits"]), str(r["gens_done"]),
                str(r["revenue_rub"]), esc(products_str), esc(sub_str),
                esc(r["last_activity_at"]), esc(r["source"]), esc(r["created_at"]),
            ]))
        return PlainTextResponse(
            "﻿" + "\n".join(lines),
            headers={
                "Content-Disposition": 'attachment; filename="clients.csv"',
                "Content-Type": "text/csv; charset=utf-8",
            },
        )

    # ── Subscriptions: list + manual charge trigger ─────────────────────

    @app.get("/admin/subscriptions", response_class=HTMLResponse)
    async def subscriptions_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        from datetime import datetime as _dt, timezone as _tz

        ok_msg = html_mod.escape(str(request.query_params.get("ok", "")).strip())
        err_msg = html_mod.escape(str(request.query_params.get("err", "")).strip())

        subs, summary, orphans = await asyncio.gather(
            credits_db.list_active_subscriptions(),
            credits_db.subscriptions_summary(),
            credits_db.find_orphan_recurrent_payments(),
        )

        # Loop liveness from Redis heartbeat.
        last_tick_str = ""
        last_tick_age_str = ""
        last_stats_str = ""
        loop_health_color = "#8d86ad"
        try:
            redis = state_store.redis
            ht = await redis.get("tg_bot_public:sub_charge_loop:last_tick")
            stats_raw = await redis.get("tg_bot_public:sub_charge_loop:last_stats")
            if ht:
                last_tick_str = str(ht)
                try:
                    ts = _dt.fromisoformat(str(ht))
                    age_s = (_dt.now(_tz.utc) - ts).total_seconds()
                    if age_s < 3600:
                        last_tick_age_str = f"{int(age_s // 60)} мин назад"
                        loop_health_color = "#4ecf8a"
                    elif age_s < 30 * 3600:
                        last_tick_age_str = f"{int(age_s // 3600)} ч назад"
                        loop_health_color = "#4ecf8a"
                    else:
                        last_tick_age_str = f"{int(age_s // 3600)} ч назад — проверь воркер!"
                        loop_health_color = "#ef6a6a"
                except Exception:
                    pass
            if stats_raw:
                try:
                    s = json.loads(str(stats_raw))
                    last_stats_str = f"{s.get('succeeded', 0)} ок / {s.get('failed', 0)} фейл (instance: {s.get('instance', '?')})"
                except Exception:
                    pass
        except Exception as e:
            log.warning("subs loop heartbeat read failed: %s", e)

        now_utc = _dt.now(_tz.utc)
        rows_html = ""
        for s in subs:
            uname = f"@{s['username']}" if s["username"] else str(s["tg_id"])
            nc_raw = s.get("_next_charge_raw")
            if hasattr(nc_raw, "tzinfo"):
                nc_dt = nc_raw if nc_raw.tzinfo else nc_raw.replace(tzinfo=_tz.utc)
                delta_days = (nc_dt - now_utc).total_seconds() / 86400.0
                if delta_days < 0:
                    when_lbl = f'<span style="color:#ef6a6a">просрочено на {abs(int(delta_days))} д</span>'
                elif delta_days < 1:
                    when_lbl = '<span style="color:#f0b04a">сегодня</span>'
                elif delta_days < 7:
                    when_lbl = f'<span style="color:#f0b04a">через {int(delta_days)}д</span>'
                else:
                    when_lbl = f'через {int(delta_days)}д'
            else:
                when_lbl = "—"
            status_cls = {"active": "badge-ok", "paused": "badge-zero"}.get(s["status"], "badge-stage")
            status_lbl = {"active": "активна", "paused": "на паузе"}.get(s["status"], s["status"])
            last_color = {"CONFIRMED": "#4ecf8a", "charge_failed": "#ef6a6a", "REJECTED": "#ef6a6a"}.get(s["last_charge_status"], "#8d86ad")
            last_lbl = {"CONFIRMED": "прошло", "charge_failed": "не прошло", "REJECTED": "отклонено"}.get(s["last_charge_status"], s["last_charge_status"])
            last_html = (
                f'<span style="color:{last_color}">{html_mod.escape(last_lbl)}</span>'
                f' <small style="color:#8d86ad">{html_mod.escape(s["last_charge_at"])}</small>'
                if s["last_charge_status"] else '<span style="color:#8d86ad">—</span>'
            )
            rb = s["rebill_id"]
            rb_masked = (rb[:4] + "…" + rb[-4:]) if len(rb) > 8 else rb
            rows_html += (
                f"<tr>"
                f"<td><a href='/admin/users/{s['tg_id']}'>{html_mod.escape(uname)}</a></td>"
                f"<td>{html_mod.escape(s['package'])}</td>"
                f"<td>{s['amount_rub']}₽</td>"
                f"<td><span class='badge {status_cls}'>{status_lbl}</span></td>"
                f"<td>{html_mod.escape(s['next_charge_at'])}<br><small>{when_lbl}</small></td>"
                f"<td>{last_html}</td>"
                f"<td>{s['charge_retries']}</td>"
                f"<td><code style='font-size:0.75em'>{html_mod.escape(rb_masked)}</code></td>"
                f"<td>"
                f"<form method='post' action='/admin/subscriptions/{s['id']}/charge' style='display:inline' "
                f"onsubmit=\"return confirm('Списать сейчас? Карта будет реально списана.')\">"
                f"<button type='submit' class='btn-secondary btn-sm'>Списать</button>"
                f"</form>"
                + (
                    f" <form method='post' action='/admin/subscriptions/{s['id']}/cancel' style='display:inline' "
                    f"onsubmit=\"return confirm('Отменить подписку? Списаний больше не будет.')\">"
                    f"<button type='submit' class='btn-danger btn-sm'>Отменить</button>"
                    f"</form>"
                    if s["status"] == "active" else ""
                )
                + f"</td>"
                f"</tr>"
            )

        flash = ""
        if ok_msg:
            flash = f'<div class="card" style="background:rgba(52,211,153,.12);border-color:rgba(52,211,153,.4)">{ok_msg}</div>'
        elif err_msg:
            flash = f'<div class="card" style="background:rgba(251,113,133,.12);border-color:rgba(251,113,133,.4)">{err_msg}</div>'

        orphan_recoverable = [p for p in orphans if str(p.get("rebill_id", "")).strip()]
        paid_without_autopay = [p for p in orphans if not str(p.get("rebill_id", "")).strip()]
        orphan_cards = ""

        def _orphan_user_link(p: dict[str, Any]) -> str:
            uname = str(p.get("username", "") or "").strip()
            label = f"@{uname}" if uname else str(p["tg_id"])
            return f"<a href='/admin/users/{p['tg_id']}'>{html_mod.escape(label)}</a>"

        if orphan_recoverable:
            recoverable_rows = ""
            for p in orphan_recoverable[:50]:
                rb = str(p.get("rebill_id", "") or "")
                rb_masked = (rb[:4] + "…" + rb[-4:]) if len(rb) > 8 else rb
                recoverable_rows += (
                    f"<tr><td>{_orphan_user_link(p)}</td>"
                    f"<td>{html_mod.escape(p['package'])}</td>"
                    f"<td>{p['amount_rub']}₽</td>"
                    f"<td><code style='font-size:0.75em'>{html_mod.escape(p['order_id'])}</code></td>"
                    f"<td><code style='font-size:0.75em'>{html_mod.escape(rb_masked)}</code></td>"
                    f"<td>{html_mod.escape(p['created_at'])}</td>"
                    f"<td><form method='post' action='/admin/subscriptions/recover' style='display:inline' "
                    f"onsubmit=\"return confirm('Создать активную подписку из сохранённого RebillId?')\">"
                    f"<input type='hidden' name='order_id' value='{html_mod.escape(p['order_id'], quote=True)}'>"
                    f"<button type='submit' class='btn-success' style='font-size:0.8em;padding:4px 8px'>Создать подписку</button>"
                    f"</form></td></tr>"
                )
            orphan_cards += f"""
            <div class="card" style="border:2px solid #4ecf8a">
              <h2 style="color:#4ecf8a">Recoverable: есть RebillId, но нет подписки ({len(orphan_recoverable)})</h2>
              <p>Оплата уже подтверждена, ключ карты сохранён в <code>payments.rebill_id</code>, но строки в
                 <code>subscriptions</code> нет. Эти кейсы можно восстановить прямо отсюда: кнопка создаст активную
                 подписку, следующее списание будет через стандартный месяц.</p>
              <div class="table-wrap">
              <table><tr><th>Юзер</th><th>Пакет</th><th>Сумма</th><th>Order</th><th>RebillId</th><th>Когда оплачено</th><th></th></tr>
              {recoverable_rows}</table>
              </div>
            </div>
            """

        if paid_without_autopay:
            no_autopay_rows = ""
            for p in paid_without_autopay[:50]:
                no_autopay_rows += (
                    f"<tr><td>{_orphan_user_link(p)}</td>"
                    f"<td>{html_mod.escape(p['package'])}</td>"
                    f"<td>{p['amount_rub']}₽</td>"
                    f"<td><code style='font-size:0.75em'>{html_mod.escape(p['order_id'])}</code></td>"
                    f"<td>{html_mod.escape(p['created_at'])}</td>"
                    f"<td><span class='badge badge-warn'>RebillId пустой</span></td>"
                    f"<td><form method='post' action='/admin/subscriptions/fetch-rebill' style='display:inline' "
                    f"onsubmit=\"return confirm('Запросить RebillId у T-Bank и создать подписку?')\">"
                    f"<input type='hidden' name='order_id' value='{html_mod.escape(p['order_id'], quote=True)}'>"
                    f"<button type='submit' class='btn-success' style='font-size:0.8em;padding:4px 8px'>"
                    f"Найти RebillId в T-Bank</button></form></td></tr>"
                )
            orphan_cards += f"""
            <div class="card" style="border:2px solid #f0b04a">
              <h2 style="color:#f0b04a">Оплачено, но без автосписания ({len(paid_without_autopay)})</h2>
              <p><b>Это не неоплаченные платежи.</b> Статус платежа <code>CONFIRMED</code>; в этом блоке нет активной
                 подписки, потому что T-Bank не дал <code>RebillId</code>. Чаще всего это QR/SBP или notification без
                 ключа карты. Автосписание по таким строкам не стартует.</p>
              <p>Кнопка <b>«Найти RebillId в T-Bank»</b> спрашивает <code>GetCardList</code> по
                 <code>CustomerKey</code> юзера: если карта всё-таки привязалась, а потерялась только нотификация —
                 ключ найдётся и подписка создастся сразу. Если T-Bank отвечает «карт нет» — привязки не было
                 (СБП/QR/T-Pay), тут поможет только новая оплата картой.</p>
              <div class="table-wrap">
              <table><tr><th>Юзер</th><th>Пакет</th><th>Сумма</th><th>Order</th><th>Когда оплачено</th><th>Причина</th><th></th></tr>
              {no_autopay_rows}</table>
              </div>
            </div>
            """

        body = f"""
        {flash}
        {orphan_cards}
        <div class="card">
          <div class="toolbar">
            <h2 style="margin:0">Автосписания</h2>
            <span class="status-pill {'ok' if loop_health_color == '#4ecf8a' else 'warn' if loop_health_color == '#f0b04a' else 'bad' if loop_health_color == '#ef6a6a' else ''}"><i></i>последний проход {html_mod.escape(last_tick_age_str or '—')}</span>
          </div>
          <div class="stage-grid" style="margin-top:14px">
            <div class="stage-chip"><div class="count" style="color:var(--ok)">{summary.get('recurrent_ok_30d', 0)}</div><div class="label">списаний за 30 дней</div></div>
            <div class="stage-chip"><div class="count" style="color:{'var(--danger)' if summary.get('recurrent_fail_30d', 0) else 'var(--text)'}">{summary.get('recurrent_fail_30d', 0)}</div><div class="label">не прошло</div></div>
            <div class="stage-chip"><div class="count">{summary.get('recurrent_revenue_30d', 0):,}&nbsp;&#8381;</div><div class="label">выручка с подписок за 30 дней</div></div>
          </div>
        </div>

        <div class="card">
          <h2>Ближайшие списания</h2>
          <div class="stage-grid">
            <div class="stage-chip"><div class="count" style="color:var(--ok)">{summary.get('active_cnt', 0)}</div><div class="label">активных подписок</div></div>
            <div class="stage-chip"><div class="count" style="color:{'var(--danger)' if summary.get('paused_cnt', 0) else 'var(--text)'}">{summary.get('paused_cnt', 0)}</div><div class="label">на паузе</div></div>
            <div class="stage-chip"><div class="count" style="color:{'var(--warn)' if summary.get('overdue_cnt', 0) else 'var(--text)'}">{summary.get('overdue_cnt', 0)}</div><div class="label">просрочены</div></div>
            <div class="stage-chip"><div class="count">{summary.get('due_today_cnt', 0)}</div><div class="label">сегодня · {summary.get('due_today_rub', 0):,}&nbsp;&#8381;</div></div>
            <div class="stage-chip"><div class="count">{summary.get('due_7d_cnt', 0)}</div><div class="label">за 7 дней · {summary.get('due_7d_rub', 0):,}&nbsp;&#8381;</div></div>
            <div class="stage-chip"><div class="count">{summary.get('due_this_month_cnt', 0)}</div><div class="label">в этом месяце · {summary.get('due_this_month_rub', 0):,}&nbsp;&#8381;</div></div>
          </div>
        </div>

        <div class="card">
          <div class="toolbar">
            <h2 style="margin:0">Все подписки</h2>
            <form method="post" action="/admin/subscriptions/dedup" style="display:inline"
                  onsubmit="return confirm('Оставить только самую новую активную подписку на каждого пользователя?')">
              <button type="submit" class="btn-secondary btn-sm">Убрать дубли</button>
            </form>
          </div>
          <div class="table-wrap">
          <table>
            <tr>
              <th>Клиент</th><th>Пакет</th><th>Сумма</th><th>Статус</th>
              <th>Следующее списание</th><th>Последнее списание</th><th>Попытки</th><th>Карта</th><th></th></tr>
            {rows_html if rows_html else '<tr><td colspan="9">Подписок ещё нет.</td></tr>'}
          </table>
          </div>
        </div>
        """
        return _page("Подписки", body)

    @app.post("/admin/subscriptions/dedup")
    async def subscriptions_dedup(
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        cancelled = await credits_db.dedup_active_subscriptions()
        if cancelled:
            await credits_db.audit_log(_user, "subscriptions_dedup", details=f"cancelled={cancelled}")
            return RedirectResponse(
                f"/admin/subscriptions?ok={quote_plus(f'Дедуп: отменено {cancelled} дублей')}",
                status_code=303,
            )
        return RedirectResponse(
            f"/admin/subscriptions?ok={quote_plus('Дублей нет, всё чисто')}",
            status_code=303,
        )

    @app.post("/admin/subscriptions/recover")
    async def subscriptions_recover_from_payment(
        order_id: str = Form(...),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        clean_order_id = str(order_id or "").strip()
        if not clean_order_id:
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('Order ID пустой')}",
                status_code=303,
            )

        payment = await credits_db.get_payment(clean_order_id)
        if not payment:
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('Платёж не найден')}",
                status_code=303,
            )

        tg_id = int(payment["tg_id"])
        if str(payment.get("status", "")).strip().upper() != "CONFIRMED":
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('Платёж ещё не CONFIRMED')}",
                status_code=303,
            )
        if not bool(payment.get("is_recurrent", False)):
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('Платёж не recurrent')}",
                status_code=303,
            )

        rebill_id = str(payment.get("rebill_id", "") or "").strip()
        if not rebill_id:
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('У платежа нет RebillId — нажми «Найти RebillId в T-Bank»')}",
                status_code=303,
            )

        active_sub = await credits_db.get_active_subscription(tg_id)
        if active_sub:
            existing_sub_id = active_sub.get("id", "?")
            return RedirectResponse(
                f"/admin/subscriptions?ok={quote_plus(f'У пользователя уже есть активная подписка sub={existing_sub_id}')}",
                status_code=303,
            )

        pkg = str(payment.get("package", "") or "")
        amount_rub = int(payment.get("amount_rub", 0) or 0)
        await credits_db.create_subscription(tg_id, pkg, rebill_id, amount_rub)
        await credits_db.log_event(
            tg_id,
            "subscription_created",
            f"{pkg} rebill=***{rebill_id[-6:]} admin_recover order={clean_order_id}",
        )
        try:
            await credits_db.audit_log(
                _user,
                "subscription_recovered",
                target=str(tg_id),
                details=f"order={clean_order_id}",
            )
        except Exception as e:
            log.warning("subscription recover audit failed order=%s err=%s", clean_order_id, e)
        return RedirectResponse(
            f"/admin/subscriptions?ok={quote_plus(f'Подписка восстановлена для {tg_id}')}",
            status_code=303,
        )

    @app.post("/admin/subscriptions/fetch-rebill")
    async def subscriptions_fetch_rebill_from_tbank(
        order_id: str = Form(...),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        """Recover a lost RebillId straight from T-Bank via GetCardList.

        Covers the case the recover button cannot: the payment is CONFIRMED but
        `payments.rebill_id` is empty because the notification carrying the key
        never landed. The card is still bound to CustomerKey on T-Bank's side,
        so the key is fetchable — unless no card was ever bound (SBP/QR/T-Pay),
        in which case only a fresh card payment can start autopay.
        """
        clean_order_id = str(order_id or "").strip()
        if not clean_order_id:
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('Order ID пустой')}",
                status_code=303,
            )
        if not tbank_client:
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('T-Bank клиент не сконфигурирован')}",
                status_code=303,
            )

        payment = await credits_db.get_payment(clean_order_id)
        if not payment:
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('Платёж не найден')}",
                status_code=303,
            )
        if str(payment.get("status", "")).strip().upper() != "CONFIRMED":
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('Платёж ещё не CONFIRMED')}",
                status_code=303,
            )
        if not bool(payment.get("is_recurrent", False)):
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('Платёж не recurrent')}",
                status_code=303,
            )

        tg_id = int(payment["tg_id"])
        # CustomerKey is the chat id — see _show_purchase_stub in app.py.
        try:
            rebill_id = await tbank_client.find_rebill_id(str(tg_id))
        except Exception as e:
            log.error("fetch-rebill: GetCardList failed order=%s err=%s", clean_order_id, e)
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus(f'GetCardList упал: {e}')}",
                status_code=303,
            )
        if not rebill_id:
            return RedirectResponse(
                "/admin/subscriptions?err=" + quote_plus(
                    "T-Bank не вернул привязанных карт для этого юзера — карта не привязывалась "
                    "(СБП/QR/T-Pay). Автосписание можно завести только новой оплатой картой."
                ),
                status_code=303,
            )

        await credits_db.update_rebill_id(clean_order_id, rebill_id)
        masked = f"***{rebill_id[-6:]}"

        active_sub = await credits_db.get_active_subscription(tg_id)
        if active_sub:
            existing_sub_id = active_sub.get("id", "?")
            return RedirectResponse(
                "/admin/subscriptions?ok=" + quote_plus(
                    f"RebillId {masked} сохранён; активная подписка sub={existing_sub_id} уже была"
                ),
                status_code=303,
            )

        pkg = str(payment.get("package", "") or "")
        amount_rub = int(payment.get("amount_rub", 0) or 0)
        await credits_db.create_subscription(tg_id, pkg, rebill_id, amount_rub)
        await credits_db.log_event(
            tg_id,
            "subscription_created",
            f"{pkg} rebill={masked} tbank_cardlist order={clean_order_id}",
        )
        try:
            await credits_db.audit_log(
                _user,
                "subscription_rebill_fetched",
                target=str(tg_id),
                details=f"order={clean_order_id} rebill={masked}",
            )
        except Exception as e:
            log.warning("fetch-rebill audit failed order=%s err=%s", clean_order_id, e)
        log.info(
            "fetch-rebill: subscription created order=%s tg_id=%s rebill=%s",
            clean_order_id, tg_id, masked,
        )
        return RedirectResponse(
            "/admin/subscriptions?ok=" + quote_plus(
                f"RebillId {masked} найден в T-Bank, подписка создана для {tg_id}"
            ),
            status_code=303,
        )

    @app.post("/admin/subscriptions/{sub_id}/cancel")
    async def subscriptions_admin_cancel(
        sub_id: int, _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        sub = await credits_db.get_subscription_by_id(sub_id)
        if not sub:
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('Подписка не найдена')}",
                status_code=303,
            )
        if sub["status"] != "active":
            sub_status = sub["status"]
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus(f'Подписка уже не активна: {sub_status}')}",
                status_code=303,
            )
        ok = await credits_db.cancel_subscription(sub["tg_id"])
        if ok:
            await credits_db.log_event(
                sub["tg_id"], "subscription_cancelled", f"by admin {_user}",
            )
            await credits_db.audit_log(
                _user, "subscription_cancelled",
                target=str(sub_id), details=f"tg_id={sub['tg_id']}",
            )
            return RedirectResponse(
                f"/admin/subscriptions?ok={quote_plus(f'Подписка sub={sub_id} отменена')}",
                status_code=303,
            )
        return RedirectResponse(
            f"/admin/subscriptions?err={quote_plus('Не удалось отменить')}",
            status_code=303,
        )

    @app.post("/admin/subscriptions/{sub_id}/charge")
    async def subscriptions_manual_charge(
        sub_id: int, request: Request, _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        redirect_back = request.headers.get("referer") or "/admin/subscriptions"

        if bot_app is None or not hasattr(bot_app, "charge_subscription_once"):
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('Бот недоступен (bot_app не передан)')}",
                status_code=303,
            )
        sub = await credits_db.get_subscription_by_id(sub_id)
        if not sub:
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('Подписка не найдена')}",
                status_code=303,
            )
        if sub["status"] != "active":
            sub_status = sub["status"]
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus(f'Статус подписки: {sub_status}')}",
                status_code=303,
            )
        if not sub["rebill_id"]:
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus('У подписки нет RebillId')}",
                status_code=303,
            )

        try:
            success, err = await bot_app.charge_subscription_once(sub, manual=True)
        except Exception as e:
            log.exception("manual charge failed sub=%s", sub_id)
            await credits_db.audit_log(_user, "subscription_manual_charge_error", str(sub_id), str(e))
            return RedirectResponse(
                f"/admin/subscriptions?err={quote_plus(f'Ошибка: {e}')}",
                status_code=303,
            )

        if success:
            await credits_db.audit_log(_user, "subscription_manual_charge_ok", str(sub_id), f"tg_id={sub['tg_id']}")
            target = redirect_back if "/admin/users/" in redirect_back else "/admin/subscriptions"
            return RedirectResponse(
                f"{target}?ok={quote_plus(f'Списание sub={sub_id} прошло успешно')}",
                status_code=303,
            )
        await credits_db.audit_log(_user, "subscription_manual_charge_fail", str(sub_id), err or "")
        return RedirectResponse(
            f"/admin/subscriptions?err={quote_plus(f'Списание не прошло: {err}')}",
            status_code=303,
        )

    # ── User card extensions: tags, notes, send message ───────────────

    @app.post("/admin/users/{tg_id}/tags/add")
    async def user_tag_add(tg_id: int, tag: str = Form(...), _user: str = Depends(_check_auth)) -> RedirectResponse:
        ok = await credits_db.add_user_tag(tg_id, tag, created_by=_user)
        if ok:
            await credits_db.audit_log(_user, "tag_add", str(tg_id), tag)
        return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)

    @app.post("/admin/users/{tg_id}/tags/remove")
    async def user_tag_remove(tg_id: int, tag: str = Form(...), _user: str = Depends(_check_auth)) -> RedirectResponse:
        ok = await credits_db.remove_user_tag(tg_id, tag)
        if ok:
            await credits_db.audit_log(_user, "tag_remove", str(tg_id), tag)
        return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)

    @app.post("/admin/users/{tg_id}/notes/add")
    async def user_note_add(tg_id: int, note: str = Form(...), _user: str = Depends(_check_auth)) -> RedirectResponse:
        nid = await credits_db.add_user_note(tg_id, note, created_by=_user)
        if nid:
            await credits_db.audit_log(_user, "note_add", str(tg_id), f"id={nid}")
        return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)

    @app.post("/admin/users/{tg_id}/notes/{nid}/delete")
    async def user_note_delete(tg_id: int, nid: int, _user: str = Depends(_check_auth)) -> RedirectResponse:
        if await credits_db.delete_user_note(nid):
            await credits_db.audit_log(_user, "note_delete", str(tg_id), f"id={nid}")
        return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)

    @app.post("/admin/users/{tg_id}/manual-payment/add")
    async def user_manual_payment_add(
        tg_id: int,
        amount_rub: int = Form(...),
        note: str = Form(""),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        if amount_rub == 0:
            return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)
        mpid = await credits_db.add_manual_payment(tg_id, amount_rub, note=note, created_by=_user)
        if mpid:
            await credits_db.audit_log(_user, "manual_payment_add", str(tg_id), f"id={mpid} {amount_rub}rub")
        return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)

    @app.post("/admin/users/{tg_id}/manual-payment/{mpid}/delete")
    async def user_manual_payment_delete(
        tg_id: int, mpid: int, _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        if await credits_db.delete_manual_payment(mpid):
            await credits_db.audit_log(_user, "manual_payment_delete", str(tg_id), f"id={mpid}")
        return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)

    @app.post("/admin/users/{tg_id}/message")
    async def user_send_message(
        tg_id: int, text: str = Form(...), parse_mode: str = Form("HTML"),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        if not bot_ref or not bot_ref[0]:
            raise HTTPException(503, "Bot not ready")
        msg = str(text or "").strip()
        if not msg:
            return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)
        try:
            await send_bot_message(bot_ref[0], tg_id, text=msg, parse_mode=parse_mode)
            await credits_db.audit_log(_user, "dm_send", str(tg_id), f"len={len(msg)}")
            await credits_db.log_event(tg_id, "admin_dm", f"by={_user}")
        except Exception as e:
            await credits_db.audit_log(_user, "dm_fail", str(tg_id), str(e)[:200])
            raise HTTPException(500, f"Send failed: {e}")
        return RedirectResponse(f"/admin/users/{tg_id}", status_code=303)

    # ── Tier system: classification page + outreach workflow ─────────

    async def _resolve_tier_count(tier_code: str, primary_counts: Dict[str, int]) -> int:
        """Get count for a tier — uses primary view counts for primary tiers,
        and the fast audience-only count resolver for P2/D1/D2.
        """
        spec = _TIER_SPEC.get(tier_code)
        if not spec:
            return 0
        if spec.get("audience_only"):
            return await credits_db.audience_only_tier_count(tier_code)
        return int(primary_counts.get(tier_code, 0))

    async def _resolve_tier_users(tier_code: str, limit: int = 2000) -> List[Dict[str, Any]]:
        spec = _TIER_SPEC.get(tier_code)
        if not spec:
            return []
        if spec.get("audience_only"):
            return await credits_db.list_audience_only_tier_users(tier_code, limit=limit)
        return await credits_db.list_tier_users(tier_code, limit=limit)

    @app.get("/admin/tiers", response_class=HTMLResponse)
    async def tiers_view(request: Request, _user: str = Depends(_check_auth)) -> str:
        selected_tier = str(request.query_params.get("tier") or "").strip().upper()
        primary_counts = await credits_db.tier_counts()

        # Pre-compute counts for all tiers including audience-only — run in
        # parallel: 12 primary tiers are O(1) dict lookups, but the 3 audience-only
        # tiers (P2/D1/D2) hit the DB. Sequential awaits would be 3× slower.
        codes = list(_TIER_SPEC.keys())
        count_results = await asyncio.gather(
            *[_resolve_tier_count(code, primary_counts) for code in codes]
        )
        all_counts: Dict[str, int] = dict(zip(codes, count_results))

        # ── Top: tier picker grid (no checklist; tier-first UX). ──────
        groups_html = ""
        for letter, label, group_caption, color in _TIER_GROUPS:
            tiers_in_group = [(code, spec) for code, spec in _TIER_SPEC.items() if spec["group"] == letter]
            tile_html = ""
            for code, spec in tiers_in_group:
                cnt = all_counts.get(code, 0)
                kind = spec.get("kind", "auto")
                kind_emoji = {"manager": "👤", "auto": "⚡", "broadcast": "📣"}.get(kind, "•")
                is_active = code == selected_tier
                border = f"2px solid {color}" if is_active else "1px solid rgba(139,111,230,.2)"
                tile_html += (
                    f'<a href="/admin/tiers?tier={code}" '
                    f'style="display:flex;flex-direction:column;justify-content:space-between;'
                    f'min-width:170px;min-height:80px;padding:10px 12px;border-radius:8px;'
                    f'border:{border};background:#110b20;text-decoration:none;color:#f2effc">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center">'
                    f'<span class="badge" style="background:{color};color:white">{code}</span>'
                    f'<span style="font-size:1.4em;font-weight:700">{cnt}</span>'
                    f'</div>'
                    f'<div style="margin-top:6px;font-size:0.85em;color:#6a6390">'
                    f'{kind_emoji} {html_mod.escape(spec["title"])}</div>'
                    f'</a>'
                )
            groups_html += (
                f'<div class="card" style="border-left:4px solid {color}">'
                f'<h3 style="margin-top:0;color:{color}">{letter} — {html_mod.escape(label)}'
                f'<small style="color:#c6bfe4;font-weight:normal;margin-left:0.5rem">{html_mod.escape(group_caption)}</small></h3>'
                f'<div style="display:flex;flex-wrap:wrap;gap:0.6rem">{tile_html}</div>'
                f'</div>'
            )

        # ── Detail card (rendered when a tier is selected) ────────────
        detail_html = ""
        if selected_tier and selected_tier in _TIER_SPEC:
            detail_html = await _render_tier_detail(selected_tier)
        elif selected_tier:
            detail_html = (
                f'<div class="card"><p>Неизвестный тир: '
                f'<code>{html_mod.escape(selected_tier)}</code></p></div>'
            )

        legend_html = (
            '<div class="info-box" style="margin-top:0">'
            '<b>Легенда:</b> '
            '<span style="margin-right:0.8rem">👤 — менеджер 1-1</span>'
            '<span style="margin-right:0.8rem">⚡ — авто-триггер</span>'
            '<span>📣 — ручная массовая рассылка</span><br>'
            '<small>Один пользователь = один primary-тир в моменте. P2/D1/D2 — audience-only сегменты для ручных рассылок, не выводят юзера из активного S/A/B.</small>'
            '</div>'
        )
        body = f"{legend_html}{groups_html}{detail_html}"
        return _page("Тиры", body)

    async def _render_tier_detail(tier_code: str) -> str:
        """Render the per-tier detail card: spec + active triggers + recent fires + users."""
        spec = _TIER_SPEC[tier_code]
        # Run independent queries in parallel: user list, rules list, and per-tier
        # outreach lookup are all I/O-bound and don't depend on each other.
        users_rows, rules_in_tier = await asyncio.gather(
            _resolve_tier_users(tier_code, limit=2000),
            credits_db.list_lifecycle_rules(tier=tier_code),
        )
        users_count = len(users_rows)

        # Per-rule fires fetched in parallel (each rule = one query).
        fires_lists: List[List[Dict[str, Any]]] = (
            list(await asyncio.gather(*[
                credits_db.recent_lifecycle_fires(rule["id"], limit=20)
                for rule in rules_in_tier
            ]))
            if rules_in_tier else []
        )
        rules_html = await _render_tier_rules_panel(tier_code, rules_in_tier)

        # Flatten + sort + cap to 50 most recent fires across the tier's rules.
        fires_rows: List[Dict[str, Any]] = [f for sub in fires_lists for f in sub]
        fires_rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        fires_rows = fires_rows[:50]
        fires_html = _render_fires_table(fires_rows, with_rule=False)

        # Outreach map (only meaningful for manager-tiers).
        outreach_map = {}
        is_manager_tier = spec.get("kind") == "manager"
        if is_manager_tier:
            outreach_map = await credits_db.get_outreach_map(
                tier_code, [int(r["tg_id"]) for r in users_rows]
            )

        # User table.
        tbody = ""
        for r in users_rows[:200]:  # cap visual table to 200; full list via CSV
            uname = f"@{r['username']}" if r.get('username') else str(r['tg_id'])
            tg_link = f"https://t.me/{r['username']}" if r.get('username') else "#"
            rating = r.get("last_rating") or "—"
            rev_total = int(r.get("revenue_bot", 0) or 0) + int(r.get("revenue_manual", 0) or 0)

            outreach_cell = ""
            notify_cells = ""
            actions = f'<a href="{tg_link}" target="_blank" rel="noopener">→ TG</a>'
            if is_manager_tier:
                o = outreach_map.get(int(r["tg_id"]))
                if o:
                    status = o["status"]
                    status_badge = {
                        "todo": '<span class="badge badge-stage">todo</span>',
                        "contacted": '<span class="badge badge-ok">контакт</span>',
                        "converted": '<span class="badge" style="background:#4ecf8a;color:white">конверсия</span>',
                        "dropped": '<span class="badge badge-zero">дроп</span>',
                    }.get(status, status)
                    actor = html_mod.escape(o["assigned_to"] or "—")
                    outreach_cell = f"{status_badge}<br><small style='color:#c6bfe4'>{actor}</small>"
                else:
                    outreach_cell = '<span class="badge badge-stage">todo</span>'
                if tier_code == "S1":
                    notified_at = (o or {}).get("notified_at") or ""
                    notify_error = (o or {}).get("notify_last_error") or ""
                    notified_badge = (
                        '<span class="badge badge-ok">да</span>' if notified_at
                        else '<span class="badge badge-zero">нет</span>'
                    )
                    notify_cells = (
                        f"<td>{notified_badge}</td>"
                        f"<td>{html_mod.escape(notified_at or '—')}</td>"
                        f"<td><small>{html_mod.escape(notify_error or '—')}</small></td>"
                    )
                actions = (
                    f'<form method="post" action="/admin/tiers/outreach" style="display:inline">'
                    f'<input type="hidden" name="tg_id" value="{r["tg_id"]}">'
                    f'<input type="hidden" name="tier" value="{tier_code}">'
                    f'<select name="status" onchange="this.form.submit()" style="font-size:0.8em">'
                    f'<option value="">— статус —</option>'
                    f'<option value="contacted">контакт</option>'
                    f'<option value="converted">конверсия</option>'
                    f'<option value="dropped">дроп</option>'
                    f'<option value="todo">сбросить</option>'
                    f'</select></form> '
                    f'<a href="{tg_link}" target="_blank" rel="noopener">→ TG</a>'
                )

            tbody += (
                f"<tr>"
                f"<td><a href='/admin/users/{r['tg_id']}'>{html_mod.escape(uname)}</a></td>"
                f"<td>{r.get('gens_done', 0)}</td>"
                f"<td>{html_mod.escape(str(rating))}</td>"
                f"<td>{rev_total}₽</td>"
                f"<td>{html_mod.escape(r.get('cohort') or '(direct)')}</td>"
                f"<td>{html_mod.escape(_fmt_ts(r.get('last_active_at')) or '—')}</td>"
                + (f"<td>{outreach_cell}</td>" if is_manager_tier else "")
                + notify_cells
                + f"<td>{actions}</td>"
                f"</tr>"
            )

        users_table_header = (
            "<tr><th>User</th><th>gens</th><th>rating</th><th>выручка</th>"
            "<th>cohort</th><th>last_active</th>"
            + ("<th>контакт</th>" if is_manager_tier else "")
            + ("<th>менеджер уведомлён</th><th>время уведомления</th><th>ошибка уведомления</th>" if tier_code == "S1" else "")
            + "<th></th></tr>"
        )
        users_colspan = (11 if tier_code == "S1" else 8) if is_manager_tier else 7
        users_section = (
            '<div class="card">'
            f'<h3>Пользователи в тире <small style="color:#c6bfe4">— {users_count} чел'
            f' (показано {min(users_count, 200)})</small></h3>'
            f'<div class="table-wrap"><table>{users_table_header}'
            + (tbody if tbody else f'<tr><td colspan={users_colspan}>Никого нет в этом тире — самое время порадоваться.</td></tr>')
            + '</table></div></div>'
        )

        # Action buttons. "Написать менеджером" → just below the spec, links to filter
        # the user table by tier (which is already what we render).
        kind = spec.get("kind", "auto")
        if kind == "manager":
            manager_button = (
                f'<a class="btn" href="#users-list" style="background:#ef6a6a">Написать менеджером →</a>'
            )
        else:
            manager_button = ""

        # "Создать триггер" pre-fills the form for this tier with the template.
        create_trigger_btn = (
            f'<a class="btn btn-success" href="/admin/lifecycle?tier={tier_code}#new">'
            f'Создать триггер для этого тира</a>'
        )
        # Audience-only tiers don't have lifecycle triggers (manual only).
        if spec.get("audience_only"):
            create_trigger_btn = (
                '<span class="badge badge-stage" style="padding:6px 14px">'
                'Audience-only — триггеры не создаются, только рассылки</span>'
            )

        broadcast_btn = (
            f'<a class="btn btn-success" href="/admin/broadcasts/new?tier={tier_code}">Создать рассылку</a>'
        )

        spec_card = f"""
        <div class="card">
          <h3 style="margin:0">
            <span class="badge" style="background:{spec['color']};color:white">{tier_code}</span>
            {html_mod.escape(spec['title'])}
            <small style="color:#c6bfe4">— {users_count} чел · {html_mod.escape(spec.get('kind', 'auto'))}</small>
          </h3>
          <p><b>Сегментация:</b> <code>{html_mod.escape(spec['rule'])}</code></p>
          <p><b>Задача:</b> {html_mod.escape(spec['task'])}</p>
          <p><b>Канал:</b> {html_mod.escape(spec['channel'])} ({html_mod.escape(spec.get('delay', ''))})</p>
          <p><b>Угол:</b> {html_mod.escape(spec['angle'])}</p>
          <p><b>Ожид. конверсия:</b> {html_mod.escape(spec['cr'])}</p>
          <div style="margin-top:0.8rem;display:flex;flex-wrap:wrap;gap:0.5rem">
            {create_trigger_btn}
            {broadcast_btn}
            <a class="btn" href="/admin/tiers/{tier_code}/export">Скачать CSV</a>
            {manager_button}
          </div>
        </div>
        """
        recent_fires_card = (
            '<div class="card"><h3>Последние срабатывания триггеров этого тира</h3>'
            + (fires_html or '<p style="color:#c6bfe4">Пока ни одного срабатывания.</p>')
            + '</div>'
        )
        anchor = '<a id="users-list"></a>'
        return spec_card + rules_html + recent_fires_card + anchor + users_section

    async def _render_tier_rules_panel(tier_code: str, rules: List[Dict[str, Any]]) -> str:
        """Per-tier panel listing lifecycle rules attached to this tier with stats."""
        if not rules:
            return (
                '<div class="card"><h3>Активные триггеры этого тира</h3>'
                '<p style="color:#c6bfe4">Триггеров для этого тира пока нет. '
                f'<a href="/admin/lifecycle?tier={tier_code}#new">Создать первый →</a></p></div>'
            )
        # Parallel per-rule 24h stats — was the slowest part of detail rendering.
        all_stats = await asyncio.gather(
            *[credits_db.lifecycle_rule_stats_24h(rule["id"]) for rule in rules]
        )
        body = ''
        for rule, stats in zip(rules, all_stats):
            sent = stats.get("sent", 0)
            blocked = stats.get("blocked", 0)
            failed = stats.get("failed", 0)
            throttled = stats.get("throttled", 0)
            total_attempts = sent + blocked + failed
            err_pct = (100.0 * (blocked + failed) / total_attempts) if total_attempts else 0.0
            err_color = "#4ecf8a" if err_pct <= 30 else "#ef6a6a"
            enabled_badge = (
                '<span class="badge badge-ok">ВКЛ</span>' if rule["enabled"]
                else '<span class="badge badge-zero">выкл</span>'
            )
            trig_summary = _summarize_trigger(rule["trigger_type"], rule.get("trigger") or {})
            body += (
                '<div style="border:1px solid rgba(139,111,230,.2);border-radius:6px;padding:0.8rem 1rem;margin-bottom:0.6rem">'
                f'<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:0.5rem">'
                f'<div><strong>#{rule["id"]} {html_mod.escape(rule["name"])}</strong> {enabled_badge}'
                f'<br><small style="color:#c6bfe4">{html_mod.escape(trig_summary)}</small></div>'
                f'<div style="font-size:0.85em">'
                f'<span title="отправлено за 24ч">отпр: <b>{sent}</b></span> · '
                f'<span title="заблокированы">блок: <b>{blocked}</b></span> · '
                f'<span title="ошибки">ошиб: <b>{failed}</b></span> · '
                f'<span title="throttled (anti-fatigue)">throt: <b>{throttled}</b></span> · '
                f'<span style="color:{err_color}">err {err_pct:.0f}%</span>'
                f'</div></div>'
                f'<div style="margin-top:0.5rem;display:flex;gap:0.4rem;flex-wrap:wrap">'
                f'<a class="btn" href="/admin/lifecycle/{rule["id"]}/preview">Превью аудитории</a>'
                f'<form method="post" action="/admin/lifecycle/{rule["id"]}/toggle" style="display:inline">'
                f'<button class="{("btn-danger" if rule["enabled"] else "btn-success")}">{("Выключить" if rule["enabled"] else "Включить")}</button></form>'
                f'<a class="btn" href="/admin/lifecycle/{rule["id"]}">Открыть / редактировать</a>'
                f'</div>'
                '</div>'
            )
        return (
            '<div class="card"><h3>Активные триггеры этого тира</h3>'
            f'{body}'
            f'<p><a href="/admin/lifecycle?tier={tier_code}#new" class="btn">+ Добавить триггер для этого тира</a></p>'
            '</div>'
        )

    def _summarize_trigger(trigger_type: str, trig: Dict[str, Any]) -> str:
        if trigger_type == "time_after_event":
            ev = trig.get("event") or "?"
            det = trig.get("event_detail")
            hmin = trig.get("hours_min")
            hmax = trig.get("hours_max")
            block = trig.get("blocking_events") or []
            extra = []
            if trig.get("require_gens_eq") is not None:
                extra.append(f"gens={trig['require_gens_eq']}")
            if trig.get("require_gens_gte") is not None:
                extra.append(f"gens≥{trig['require_gens_gte']}")
            if trig.get("require_no_referral"):
                extra.append("no_referral")
            lr = trig.get("require_last_rating")
            if lr:
                extra.append(f"last_rating={lr if isinstance(lr, str) else '|'.join(lr)}")
            ev_str = f"{ev}={det}" if det else ev
            window = f"{hmin}-{hmax}ч" if hmax else f"≥{hmin}ч"
            block_str = f", без {','.join(block)}" if block else ""
            extra_str = f" [{', '.join(extra)}]" if extra else ""
            return f"⚡ {ev_str} {window}{block_str}{extra_str}"
        if trigger_type == "tier_membership":
            return f"⚡ юзер сейчас в тире {trig.get('tier', '?')}"
        if trigger_type == "low_balance_trial":
            return f"⚡ trial-юзер с балансом ≤ {trig.get('max_credits', 1)}"
        if trigger_type == "balance_low":
            return f"⚡ баланс {trig.get('credits_geq', 1)}–{trig.get('credits_leq', 2)}"
        if trigger_type == "inactive":
            return f"⚡ не заходил {trig.get('days', 14)} дн"
        if trigger_type == "generated_not_paid":
            return f"⚡ ≥{trig.get('min_gens', 3)} ген, не платил"
        return f"⚡ {trigger_type}"

    def _render_fires_table(rows: List[Dict[str, Any]], *, with_rule: bool) -> str:
        if not rows:
            return ""
        head = "<tr><th>Когда</th>"
        if with_rule:
            head += "<th>Правило</th><th>Тир</th>"
        head += "<th>User</th><th>Статус</th><th>Ошибка</th></tr>"
        body = ""
        for r in rows:
            uname = f"@{r['username']}" if r.get('username') else str(r['tg_id'])
            status_badge = {
                "sent": '<span class="badge badge-ok">отпр</span>',
                "blocked": '<span class="badge badge-zero">блок</span>',
                "failed": '<span class="badge badge-zero">ошиб</span>',
                "throttled": '<span class="badge badge-warn">throt</span>',
                "test": '<span class="badge badge-stage">test</span>',
            }.get(r.get("status", ""), html_mod.escape(r.get("status", "")))
            err = html_mod.escape((r.get("error") or "")[:120])
            body += "<tr>"
            body += f"<td>{html_mod.escape(_fmt_ts(r.get('created_at')))}</td>"
            if with_rule:
                body += (
                    f"<td>#{r.get('rule_id', '')} {html_mod.escape(r.get('rule_name', ''))}</td>"
                    f"<td>{html_mod.escape(r.get('rule_tier') or '—')}</td>"
                )
            body += (
                f"<td><a href='/admin/users/{r['tg_id']}'>{html_mod.escape(uname)}</a></td>"
                f"<td>{status_badge}</td>"
                f"<td><small>{err}</small></td>"
                "</tr>"
            )
        return f'<div class="table-wrap"><table>{head}{body}</table></div>'

    @app.get("/admin/tiers/{tier}/export", response_class=PlainTextResponse)
    async def tiers_export(tier: str, _user: str = Depends(_check_auth)) -> PlainTextResponse:
        tier_code = tier.strip().upper()
        if tier_code not in _TIER_SPEC:
            raise HTTPException(404, f"Unknown tier {tier_code}")
        rows = await _resolve_tier_users(tier_code, limit=10_000)
        outreach_map = await credits_db.get_outreach_map(tier_code, [int(r["tg_id"]) for r in rows])

        def esc(v: Any) -> str:
            s = str(v if v is not None else "").replace('"', '""')
            return f'"{s}"' if ("," in s or '"' in s or "\n" in s) else s

        lines = [
            "tg_id,username,tg_link,gens_done,last_rating,revenue_rub,cohort,"
            "last_active_at,outreach_status,outreach_actor,outreach_note"
        ]
        for r in rows:
            o = outreach_map.get(int(r["tg_id"])) or {}
            tg_link = f"https://t.me/{r['username']}" if r['username'] else ""
            rev_total = int(r.get("revenue_bot", 0) or 0) + int(r.get("revenue_manual", 0) or 0)
            lines.append(",".join([
                str(r["tg_id"]),
                esc(r["username"]),
                esc(tg_link),
                str(r["gens_done"]),
                esc(r.get("last_rating")),
                str(rev_total),
                esc(r.get("cohort")),
                esc(_fmt_ts(r.get("last_active_at"))),
                esc(o.get("status") or "todo"),
                esc(o.get("assigned_to")),
                esc(o.get("note")),
            ]))
        csv_text = "﻿" + "\n".join(lines)  # BOM so Excel opens UTF-8 correctly
        await credits_db.audit_log(_user, "tier_export", tier_code, f"rows={len(rows)}")
        return PlainTextResponse(
            csv_text,
            headers={
                "Content-Disposition": f'attachment; filename="tier_{tier_code}.csv"',
                "Content-Type": "text/csv; charset=utf-8",
            },
        )

    @app.post("/admin/tiers/outreach")
    async def tiers_outreach_set(
        tg_id: int = Form(...),
        tier: str = Form(...),
        status: str = Form(...),
        note: str = Form(""),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        tier_code = tier.strip().upper()
        status_clean = status.strip().lower()
        if tier_code not in _TIER_SPEC:
            raise HTTPException(404, f"Unknown tier {tier_code}")
        if status_clean not in ("todo", "contacted", "converted", "dropped"):
            return RedirectResponse(f"/admin/tiers?tier={tier_code}", status_code=303)
        await credits_db.upsert_outreach(
            tg_id=tg_id, tier=tier_code, status=status_clean,
            assigned_to=_user, note=note,
        )
        await credits_db.audit_log(_user, "outreach_set", str(tg_id), f"{tier_code}:{status_clean}")
        return RedirectResponse(f"/admin/tiers?tier={tier_code}", status_code=303)

    # ── Lifecycle rules ───────────────────────────────────────────────

    _TRIGGER_LABELS = {
        "time_after_event": "Через N часов после ивента",
        "tier_membership": "Юзер сейчас в тире X",
        "low_balance_trial": "Trial-юзер с низким балансом",
        "balance_low": "Баланс просел",
        "inactive": "Не заходил N дней",
        "generated_not_paid": "Генерил, но не платил",
    }

    @app.get("/admin/lifecycle", response_class=HTMLResponse)
    async def lifecycle_list(request: Request, _user: str = Depends(_check_auth)) -> str:
        tier_filter = str(request.query_params.get("tier") or "").strip().upper()
        rules = await credits_db.list_lifecycle_rules(tier=tier_filter or None)

        # ── Top: global summary ──
        global_stats = await credits_db.lifecycle_global_stats_24h()
        recent_global = await credits_db.lifecycle_recent_fires_global(limit=100)
        summary_card = (
            '<div class="card"><h3>Сводка за 24 часа</h3>'
            '<div style="display:flex;flex-wrap:wrap;gap:1rem">'
            f'<div class="stage-chip"><div class="count">{global_stats.get("sent", 0)}</div><div class="label">отправлено</div></div>'
            f'<div class="stage-chip"><div class="count">{global_stats.get("blocked", 0)}</div><div class="label">заблокированы</div></div>'
            f'<div class="stage-chip"><div class="count">{global_stats.get("failed", 0)}</div><div class="label">ошибки</div></div>'
            f'<div class="stage-chip"><div class="count">{global_stats.get("throttled", 0)}</div><div class="label">throttled (anti-fatigue)</div></div>'
            f'<div class="stage-chip"><div class="count">{global_stats.get("test", 0)}</div><div class="label">тестовых</div></div>'
            '</div></div>'
        )

        tier_options = "".join(
            f'<option value="{code}" {"selected" if code == tier_filter else ""}>'
            f'{code} — {html_mod.escape(spec["title"])}</option>'
            for code, spec in _TIER_SPEC.items()
        )
        filter_card = (
            '<div class="card">'
            '<form method="get" action="/admin/lifecycle">'
            '<label>Фильтр по тиру: '
            f'<select name="tier" onchange="this.form.submit()">'
            '<option value="">— все тиры —</option>'
            f'{tier_options}'
            '</select></label> '
            f'<a href="/admin/lifecycle" style="margin-left:0.5rem">сбросить</a>'
            '</form></div>'
        )

        # ── Rules list ──
        # Parallel per-rule 24h stats lookup.
        all_rule_stats = await asyncio.gather(
            *[credits_db.lifecycle_rule_stats_24h(r["id"]) for r in rules]
        ) if rules else []
        tr = []
        for r, stats in zip(rules, all_rule_stats):
            trig_lbl = _TRIGGER_LABELS.get(r["trigger_type"], r["trigger_type"])
            trig_summary = _summarize_trigger(r["trigger_type"], r.get("trigger") or {})
            sent = stats.get("sent", 0)
            blocked = stats.get("blocked", 0)
            failed = stats.get("failed", 0)
            throttled = stats.get("throttled", 0)
            total = sent + blocked + failed
            err_pct = (100.0 * (blocked + failed) / total) if total else 0.0
            err_color = "#4ecf8a" if err_pct <= 30 else "#ef6a6a"
            tier_badge = ""
            if r.get("tier"):
                tspec = _TIER_SPEC.get(r["tier"])
                bg = (tspec or {}).get("color", "#8d86ad")
                tier_badge = f'<span class="badge" style="background:{bg};color:white">{r["tier"]}</span> '
            enabled_badge = (
                '<span class="badge badge-ok">ВКЛ</span>' if r["enabled"]
                else '<span class="badge badge-zero">выкл</span>'
            )
            tr.append(
                f"<tr><td>#{r['id']}</td>"
                f"<td>{tier_badge}<a href='/admin/lifecycle/{r['id']}'>{html_mod.escape(r['name'])}</a><br>"
                f"<small style='color:#c6bfe4'>{html_mod.escape(trig_summary)}</small></td>"
                f"<td>{trig_lbl}</td>"
                f"<td>{r['cooldown_days']} дн.</td>"
                f"<td>отпр {sent} · блок {blocked} · ошиб {failed} · thr {throttled}<br>"
                f"<span style='color:{err_color};font-size:0.85em'>err {err_pct:.0f}%</span></td>"
                f"<td>{r['last_run_at'] or '—'}</td>"
                f"<td>{enabled_badge}</td>"
                f"<td>"
                f"<a class='btn' href='/admin/lifecycle/{r['id']}/preview' style='font-size:0.8em'>превью</a> "
                f"<form method='post' action='/admin/lifecycle/{r['id']}/toggle' style='display:inline'>"
                f"<button class='{('btn-danger' if r['enabled'] else 'btn-success')}' style='font-size:0.8em'>"
                f"{'выкл' if r['enabled'] else 'вкл'}</button></form> "
                f"<form method='post' action='/admin/lifecycle/{r['id']}/delete' style='display:inline' "
                f"onsubmit=\"return confirm('Удалить правило?')\">"
                f"<button class='btn-danger' style='font-size:0.8em'>удалить</button></form>"
                f"</td></tr>"
            )

        recent_html = _render_fires_table(recent_global, with_rule=True)
        recent_card = (
            '<div class="card"><h3>Последние 100 срабатываний (все правила)</h3>'
            + (recent_html or '<p style="color:#c6bfe4">Пока ни одного срабатывания.</p>')
            + '</div>'
        )

        # ── New-rule form (anchored at #new). Pre-fills from tier when ?tier=X ──
        prefill_text = ""
        prefill_name = ""
        prefill_tier = tier_filter
        prefill_trigger_type = "time_after_event"
        prefill_event = ""
        prefill_event_detail = ""
        prefill_hours_min = "1"
        prefill_hours_max = ""
        prefill_blocking = ""
        prefill_cooldown = "30"
        prefill_gens_eq = ""
        prefill_no_referral = False
        prefill_last_rating = ""
        if tier_filter and tier_filter in _TIER_SPEC:
            spec = _TIER_SPEC[tier_filter]
            prefill_text = spec.get("trigger_text") or ""
            prefill_name = f"{tier_filter} · {spec['title']}"
            tmpl = spec.get("trigger_template") or {}
            if tmpl:
                prefill_trigger_type = tmpl.get("trigger_type") or prefill_trigger_type
                trg = tmpl.get("trigger") or {}
                prefill_event = trg.get("event") or ""
                prefill_event_detail = trg.get("event_detail") or ""
                if "hours_min" in trg:
                    prefill_hours_min = str(trg["hours_min"])
                if "hours_max" in trg:
                    prefill_hours_max = str(trg["hours_max"])
                if trg.get("blocking_events"):
                    prefill_blocking = ",".join(trg["blocking_events"])
                if "cooldown_days" in tmpl:
                    prefill_cooldown = str(tmpl["cooldown_days"])
                if "require_gens_eq" in trg:
                    prefill_gens_eq = str(trg["require_gens_eq"])
                if trg.get("require_no_referral"):
                    prefill_no_referral = True
                lr = trg.get("require_last_rating")
                if isinstance(lr, str):
                    prefill_last_rating = lr
                elif isinstance(lr, list) and lr:
                    prefill_last_rating = lr[0]

        new_form = f"""
        <a id="new"></a>
        <div class="card">
          <h3>Добавить правило{(' для ' + tier_filter) if tier_filter else ''}</h3>
          <form method="post" action="/admin/lifecycle/new">
            <label>Название: <input type="text" name="name" required style="width:320px"
                   value="{html_mod.escape(prefill_name, quote=True)}"></label>
            <label style="margin-left:1rem">Тир:
              <select name="tier">
                <option value="">— без тира —</option>
                {''.join(f'<option value="{c}" {"selected" if c == prefill_tier else ""}>{c} — {html_mod.escape(s["title"])}</option>' for c, s in _TIER_SPEC.items())}
              </select>
            </label>
            <br><br>
            <label>Тип триггера:
              <select name="trigger_type" onchange="
                document.getElementById('tp-time').style.display=this.value==='time_after_event'?'block':'none';
                document.getElementById('tp-tier').style.display=this.value==='tier_membership'?'block':'none';
                document.getElementById('tp-trial').style.display=this.value==='low_balance_trial'?'block':'none';
                document.getElementById('tp-bal').style.display=this.value==='balance_low'?'block':'none';
                document.getElementById('tp-inact').style.display=this.value==='inactive'?'block':'none';
                document.getElementById('tp-gnp').style.display=this.value==='generated_not_paid'?'block':'none';
              ">
                <option value="time_after_event" {'selected' if prefill_trigger_type == 'time_after_event' else ''}>Через N часов после ивента (универсал для PDF v2.0)</option>
                <option value="tier_membership" {'selected' if prefill_trigger_type == 'tier_membership' else ''}>Юзер сейчас в тире X</option>
                <option value="low_balance_trial" {'selected' if prefill_trigger_type == 'low_balance_trial' else ''}>Trial-юзер, баланс низкий</option>
                <option value="balance_low">Баланс просел (credits между X и Y)</option>
                <option value="inactive">Не заходил N дней</option>
                <option value="generated_not_paid">Сделал ≥N генераций, не платил</option>
              </select>
            </label><br>

            <div id="tp-time" style="margin-top:0.5rem;{'display:block' if prefill_trigger_type == 'time_after_event' else 'display:none'}">
              Ивент: <input type="text" name="event" value="{html_mod.escape(prefill_event, quote=True)}" placeholder="rate_video / start / audio_uploaded / generation_done" style="width:240px">
              detail: <input type="text" name="event_detail" value="{html_mod.escape(prefill_event_detail, quote=True)}" placeholder="high / mid_low / low (опц)" style="width:120px">
              <br>
              hours_min: <input type="number" step="0.5" name="hours_min" value="{html_mod.escape(prefill_hours_min, quote=True)}" min="0" style="width:80px">
              hours_max: <input type="number" step="0.5" name="hours_max" value="{html_mod.escape(prefill_hours_max, quote=True)}" min="0" placeholder="опц" style="width:80px">
              <br>
              blocking_events: <input type="text" name="blocking_events" value="{html_mod.escape(prefill_blocking, quote=True)}" placeholder="через запятую: subscription_ok,generation_started" style="width:380px">
              <br>
              <label>require_gens_eq: <input type="number" name="require_gens_eq" placeholder="опц" min="0" style="width:70px" value="{html_mod.escape(prefill_gens_eq, quote=True)}"></label>
              <label style="margin-left:1rem"><input type="checkbox" name="require_no_referral" {'checked' if prefill_no_referral else ''}> require_no_referral</label>
              <label style="margin-left:1rem">require_last_rating:
                <select name="require_last_rating">
                  <option value="" {'selected' if not prefill_last_rating else ''}>—</option>
                  <option value="high" {'selected' if prefill_last_rating == 'high' else ''}>high</option>
                  <option value="mid_low" {'selected' if prefill_last_rating == 'mid_low' else ''}>mid_low</option>
                  <option value="low" {'selected' if prefill_last_rating == 'low' else ''}>low</option>
                </select>
              </label>
            </div>
            <div id="tp-tier" style="margin-top:0.5rem;{'display:block' if prefill_trigger_type == 'tier_membership' else 'display:none'}">
              Тир: <input type="text" name="trig_tier" placeholder="S2 / P4 / B1" style="width:80px">
            </div>
            <div id="tp-trial" style="margin-top:0.5rem;{'display:block' if prefill_trigger_type == 'low_balance_trial' else 'display:none'}">
              max_credits ≤ <input type="number" name="max_credits" value="1" min="0" style="width:70px">
            </div>
            <div id="tp-bal" style="display:none;margin-top:0.5rem">
              credits ≥ <input type="number" name="credits_geq" value="1" min="0" style="width:70px">
              credits ≤ <input type="number" name="credits_leq" value="2" min="0" style="width:70px">
            </div>
            <div id="tp-inact" style="display:none;margin-top:0.5rem">
              дней без активности ≥ <input type="number" name="inactive_days" value="14" min="1" style="width:70px">
            </div>
            <div id="tp-gnp" style="display:none;margin-top:0.5rem">
              минимум генераций ≥ <input type="number" name="min_gens" value="3" min="1" style="width:70px">
            </div>
            <br>
            <label>Cooldown (не долбить одному юзеру чаще, чем раз в N дней):
              <input type="number" name="cooldown_days" value="{html_mod.escape(prefill_cooldown, quote=True)}" min="1" style="width:70px">
            </label>
            <label style="margin-left:1rem"><input type="checkbox" name="exclude_paid" checked> exclude_paid</label>
            <label style="margin-left:1rem"><input type="checkbox" name="respect_anti_fatigue" checked> anti_fatigue</label>
            <br><br>
            <label>Сообщение:<br>
              <textarea name="message_text" rows="6" style="width:100%" required>{html_mod.escape(prefill_text)}</textarea>
            </label><br><br>
            <p style="color:#c6bfe4;font-size:0.85em">
              Правило создаётся <b>выключенным</b>. Перед включением — нажми «Превью аудитории» и «Тестовая отправка».
            </p>
            <button type="submit" class="btn-success">Создать (выключенным)</button>
          </form>
        </div>
        """

        body = (
            summary_card
            + filter_card
            + '<div class="card">'
            f'<h3>Правила{(": " + tier_filter) if tier_filter else ""}</h3>'
            '<div class="table-wrap"><table>'
            '<tr><th>#</th><th>Название · триггер</th><th>Тип</th><th>Cooldown</th>'
            '<th>За 24ч</th><th>Последний запуск</th><th>Статус</th><th>Действия</th></tr>'
            + (''.join(tr) if tr else '<tr><td colspan=8>Правил ещё нет</td></tr>')
            + '</table></div>'
            '<p style="color:#c6bfe4;font-size:0.85em">'
            'Воркер прогоняет правила автоматически каждые 5 минут. '
            'Перед каждой отправкой авто проверяются: bot_blocked · admin_dm за 7 дней · оплата (если exclude_paid) · '
            'anti-fatigue (≤1/48ч и ≤2/неделю) · cooldown правила.'
            '</p></div>'
            + recent_card
            + new_form
        )
        return _page("Lifecycle-триггеры", body)

    @app.get("/admin/lifecycle/{rid}", response_class=HTMLResponse)
    async def lifecycle_detail(
        rid: int, request: Request, _user: str = Depends(_check_auth),
    ) -> str:
        rule = await credits_db.get_lifecycle_rule(rid)
        if not rule:
            raise HTTPException(404)
        test_err = str(request.query_params.get("test_err") or "").strip()
        stats = await credits_db.lifecycle_rule_stats_24h(rid)
        fires = await credits_db.recent_lifecycle_fires(rid, limit=100)
        tier = rule.get("tier") or ""
        spec = _TIER_SPEC.get(tier) if tier else None
        tier_link = (
            f'<a href="/admin/tiers?tier={tier}">{tier} — {html_mod.escape((spec or {}).get("title", ""))}</a>'
            if tier else "(без тира)"
        )
        enabled_badge = (
            '<span class="badge badge-ok">ВКЛ</span>' if rule["enabled"]
            else '<span class="badge badge-zero">выкл</span>'
        )
        trig_summary = _summarize_trigger(rule["trigger_type"], rule.get("trigger") or {})

        # Edit form (just message + cooldown + flags + enabled).
        edit_form = f"""
        <div class="card"><h3>Редактировать</h3>
        <form method="post" action="/admin/lifecycle/{rid}/edit">
          <label>Название: <input type="text" name="name" value="{html_mod.escape(rule['name'], quote=True)}" style="width:320px" required></label><br><br>
          <label>Тир: <input type="text" name="tier" value="{html_mod.escape(tier, quote=True)}" style="width:80px"></label>
          <label style="margin-left:1rem">Cooldown: <input type="number" name="cooldown_days" value="{rule['cooldown_days']}" min="1" style="width:70px"></label>
          <label style="margin-left:1rem"><input type="checkbox" name="exclude_paid" {'checked' if rule.get('exclude_paid') else ''}> exclude_paid</label>
          <label style="margin-left:1rem"><input type="checkbox" name="respect_anti_fatigue" {'checked' if rule.get('respect_anti_fatigue') else ''}> anti_fatigue</label>
          <br><br>
          <label>Сообщение:<br>
            <textarea name="message_text" rows="8" style="width:100%" required>{html_mod.escape(rule['message_text'])}</textarea>
          </label><br><br>
          <button type="submit" class="btn-success">Сохранить</button>
        </form></div>
        """

        # Test send block.
        test_err_html = (
            f'<p style="color:#ef6a6a"><strong>Ошибка:</strong> {html_mod.escape(test_err)}</p>'
            if test_err else ""
        )
        test_form = f"""
        <div class="card">
          <h3>Тестовая отправка</h3>
          <p style="color:#c6bfe4;font-size:0.85em">
            Отправляет именно это сообщение указанному получателю. Помечается <code>test</code> в fires —
            не учитывается в cooldown, anti-fatigue и счётчиках. Прогони на себе перед массовым включением.
          </p>
          {test_err_html}
          <form method="post" action="/admin/lifecycle/{rid}/test_send">
            <input type="text" name="recipient" placeholder="tg_id или @username" required style="width:240px">
            <button type="submit" class="btn-success">Отправить тестово</button>
          </form>
        </div>
        """

        fires_html = _render_fires_table(fires, with_rule=False)
        fires_card = (
            '<div class="card"><h3>Последние 100 срабатываний</h3>'
            + (fires_html or '<p style="color:#c6bfe4">Пока ни одного срабатывания.</p>')
            + '</div>'
        )

        body = f"""
        <div class="card">
          <h3>#{rid} · {html_mod.escape(rule['name'])} {enabled_badge}</h3>
          <p><b>Тир:</b> {tier_link}</p>
          <p><b>Триггер:</b> {html_mod.escape(rule['trigger_type'])} — {html_mod.escape(trig_summary)}</p>
          <p><b>JSON:</b> <code>{html_mod.escape(json.dumps(rule.get('trigger') or {{}}, ensure_ascii=False))}</code></p>
          <p><b>Cooldown:</b> {rule['cooldown_days']} дн ·
             <b>exclude_paid:</b> {rule.get('exclude_paid')} ·
             <b>anti_fatigue:</b> {rule.get('respect_anti_fatigue')}</p>
          <p><b>За 24ч:</b> отправлено {stats.get('sent', 0)} · заблокированы {stats.get('blocked', 0)}
             · ошибки {stats.get('failed', 0)} · throttled {stats.get('throttled', 0)} · test {stats.get('test', 0)}</p>
          <p><b>Всего срабатываний:</b> {rule['fired_count']} · последний запуск: {rule['last_run_at'] or '—'}</p>
          <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-top:0.5rem">
            <a class="btn" href="/admin/lifecycle/{rid}/preview">Превью аудитории</a>
            <form method="post" action="/admin/lifecycle/{rid}/toggle" style="display:inline">
              <button class="{('btn-danger' if rule['enabled'] else 'btn-success')}">{'Выключить' if rule['enabled'] else 'Включить'}</button>
            </form>
            <form method="post" action="/admin/lifecycle/{rid}/delete" style="display:inline"
                  onsubmit="return confirm('Удалить правило?')">
              <button class="btn-danger">Удалить</button>
            </form>
          </div>
        </div>
        {test_form}
        {edit_form}
        {fires_card}
        """
        return _page(f"Триггер #{rid}", body, back=("Триггеры", "/admin/lifecycle"))

    @app.get("/admin/lifecycle/{rid}/preview", response_class=HTMLResponse)
    async def lifecycle_preview(rid: int, _user: str = Depends(_check_auth)) -> str:
        rule = await credits_db.get_lifecycle_rule(rid)
        if not rule:
            raise HTTPException(404)
        breakdown = await credits_db.lifecycle_audience_breakdown(rule)

        sample_html = ""
        if breakdown["sample"]:
            rows = ""
            for s in breakdown["sample"]:
                uname = f"@{s['username']}" if s["username"] else str(s["tg_id"])
                tg_link = f"https://t.me/{s['username']}" if s["username"] else "#"
                rows += (
                    f"<tr>"
                    f"<td><a href='/admin/users/{s['tg_id']}'>{html_mod.escape(uname)}</a></td>"
                    f"<td>{s['gens_done']}</td>"
                    f"<td>{html_mod.escape(s['last_rating'] or '—')}</td>"
                    f"<td>{html_mod.escape(s['last_active_at'] or '—')}</td>"
                    f"<td><a href='{tg_link}' target='_blank' rel='noopener'>→ TG</a></td>"
                    f"</tr>"
                )
            sample_html = (
                '<div class="table-wrap"><table>'
                '<tr><th>User</th><th>gens</th><th>rating</th><th>last_active</th><th></th></tr>'
                f'{rows}</table></div>'
            )
        else:
            sample_html = '<p style="color:#c6bfe4">Сейчас аудитория пустая.</p>'

        excl = breakdown
        bar_total = max(1, excl["matched_raw"])
        def pct(n: int) -> int:
            return int(round(100 * n / bar_total))

        body = f"""
        <div class="card">
          <h3>Превью аудитории · правило #{rid} {html_mod.escape(rule['name'])}</h3>
          <p>Матчей правила без exclusion: <b>{excl['matched_raw']}</b></p>
          <p>Из них убрано:</p>
          <ul>
            <li>bot_blocked: <b>{excl['excluded_blocked']}</b> ({pct(excl['excluded_blocked'])}%)</li>
            <li>admin_dm за 7 дней: <b>{excl['excluded_admin_dm']}</b> ({pct(excl['excluded_admin_dm'])}%)</li>
            <li>оплата (exclude_paid): <b>{excl['excluded_paid']}</b> ({pct(excl['excluded_paid'])}%)</li>
            <li>anti-fatigue (1/48ч, 2/нед): <b>{excl['excluded_anti_fatigue']}</b> ({pct(excl['excluded_anti_fatigue'])}%)</li>
            <li>cooldown правила: <b>{excl['excluded_cooldown']}</b> ({pct(excl['excluded_cooldown'])}%)</li>
          </ul>
          <p style="font-size:1.1em">→ <b>{excl['final_count']}</b> юзеров получат сообщение при ближайшем тике.</p>
        </div>
        <div class="card">
          <h3>Первые 10 кандидатов</h3>
          {sample_html}
        </div>
        """
        return _page(f"Превью #{rid}", body, back=[("Триггеры", "/admin/lifecycle"), (f"Триггер #{rid}", f"/admin/lifecycle/{rid}")])

    @app.post("/admin/lifecycle/{rid}/test_send")
    async def lifecycle_test_send(
        rid: int,
        recipient: str = Form(...),
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        rule = await credits_db.get_lifecycle_rule(rid)
        if not rule:
            raise HTTPException(404)
        # Accept either a numeric tg_id or @username (with or without leading @).
        raw = str(recipient or "").strip()
        tg_id: Optional[int] = None
        if raw.lstrip("-").isdigit():
            tg_id = int(raw)
        else:
            uname = raw.lstrip("@").strip().lower()
            if uname:
                matches = await credits_db.search_users(uname, limit=5)
                # Prefer exact username match; fall back to first hit otherwise.
                exact = next((m for m in matches if str(m.get("username") or "").lower() == uname), None)
                pick = exact or (matches[0] if matches else None)
                if pick:
                    tg_id = int(pick["tg_id"])
        if tg_id is None:
            await credits_db.audit_log(_user, "lifecycle_test_send_fail", str(rid), f"unresolved:{raw[:80]}")
            return RedirectResponse(
                f"/admin/lifecycle/{rid}?test_err={quote_plus('не нашёл юзера: ' + raw)}",
                status_code=303,
            )
        bot = bot_ref[0] if bot_ref else None
        if bot is None:
            await credits_db.audit_log(_user, "lifecycle_test_send_fail", str(rid), "bot not ready")
            return RedirectResponse(f"/admin/lifecycle/{rid}", status_code=303)
        try:
            await send_bot_message(
                bot, int(tg_id),
                text=rule["message_text"], parse_mode=rule.get("parse_mode", "HTML"),
            )
            await credits_db.record_lifecycle_fire(rid, int(tg_id), "test", f"manual_test_send by={_user}")
            await credits_db.audit_log(_user, "lifecycle_test_send", str(rid), f"tg_id={tg_id} src={raw[:60]}")
        except Exception as e:
            await credits_db.record_lifecycle_fire(rid, int(tg_id), "failed", f"test_send:{e}"[:400])
            await credits_db.audit_log(_user, "lifecycle_test_send_fail", str(rid), str(e)[:200])
        return RedirectResponse(f"/admin/lifecycle/{rid}", status_code=303)

    @app.post("/admin/lifecycle/new")
    async def lifecycle_new(
        request: Request,
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name") or "").strip()
        tier = str(form.get("tier") or "").strip().upper()
        trigger_type = str(form.get("trigger_type") or "").strip()
        cooldown_days = int(str(form.get("cooldown_days") or "30") or 30)
        message_text = str(form.get("message_text") or "")
        exclude_paid = bool(form.get("exclude_paid"))
        respect_anti_fatigue = bool(form.get("respect_anti_fatigue"))

        trig: dict = {}
        if trigger_type == "time_after_event":
            trig["event"] = str(form.get("event") or "").strip()
            ev_det = str(form.get("event_detail") or "").strip()
            if ev_det:
                trig["event_detail"] = ev_det
            try:
                trig["hours_min"] = float(str(form.get("hours_min") or "1") or 1)
            except ValueError:
                trig["hours_min"] = 1.0
            hmax = str(form.get("hours_max") or "").strip()
            if hmax:
                try:
                    trig["hours_max"] = float(hmax)
                except ValueError:
                    pass
            block = [b.strip() for b in str(form.get("blocking_events") or "").split(",") if b.strip()]
            if block:
                trig["blocking_events"] = block
            req_g = str(form.get("require_gens_eq") or "").strip()
            if req_g.isdigit():
                trig["require_gens_eq"] = int(req_g)
            if form.get("require_no_referral"):
                trig["require_no_referral"] = True
            req_lr = str(form.get("require_last_rating") or "").strip()
            if req_lr in ("high", "mid_low", "low"):
                trig["require_last_rating"] = req_lr
        elif trigger_type == "tier_membership":
            trig = {"tier": str(form.get("trig_tier") or "").strip().upper()}
        elif trigger_type == "low_balance_trial":
            trig = {"max_credits": int(str(form.get("max_credits") or "1") or 1)}
        elif trigger_type == "balance_low":
            trig = {
                "credits_geq": int(str(form.get("credits_geq") or "1") or 1),
                "credits_leq": int(str(form.get("credits_leq") or "2") or 2),
            }
        elif trigger_type == "inactive":
            trig = {"days": int(str(form.get("inactive_days") or "14") or 14)}
        elif trigger_type == "generated_not_paid":
            trig = {"min_gens": int(str(form.get("min_gens") or "3") or 3)}

        rid = await credits_db.create_lifecycle_rule(
            name=name, trigger_type=trigger_type, trigger=trig,
            message_text=message_text, cooldown_days=cooldown_days,
            enabled=False,  # always start disabled — admin must preview + test
            created_by=_user, tier=tier,
            exclude_paid=exclude_paid, respect_anti_fatigue=respect_anti_fatigue,
        )
        await credits_db.audit_log(_user, "lifecycle_create", str(rid), f"{trigger_type} {trig}")
        # Send admin to the rule detail page so they can immediately preview / test.
        return RedirectResponse(f"/admin/lifecycle/{rid}", status_code=303)

    @app.post("/admin/lifecycle/{rid}/edit")
    async def lifecycle_edit(
        rid: int,
        request: Request,
        _user: str = Depends(_check_auth),
    ) -> RedirectResponse:
        form = await request.form()
        rule = await credits_db.get_lifecycle_rule(rid)
        if not rule:
            raise HTTPException(404)
        await credits_db.update_lifecycle_rule(
            rid,
            name=str(form.get("name") or rule["name"]),
            tier=str(form.get("tier") or "").strip().upper(),
            cooldown_days=int(str(form.get("cooldown_days") or rule["cooldown_days"])),
            message_text=str(form.get("message_text") or rule["message_text"]),
            exclude_paid=bool(form.get("exclude_paid")),
            respect_anti_fatigue=bool(form.get("respect_anti_fatigue")),
        )
        await credits_db.audit_log(_user, "lifecycle_edit", str(rid))
        return RedirectResponse(f"/admin/lifecycle/{rid}", status_code=303)

    @app.post("/admin/lifecycle/{rid}/toggle")
    async def lifecycle_toggle(rid: int, _user: str = Depends(_check_auth)) -> RedirectResponse:
        rule = await credits_db.get_lifecycle_rule(rid)
        if not rule:
            raise HTTPException(404)
        await credits_db.update_lifecycle_rule(rid, enabled=not rule["enabled"])
        await credits_db.audit_log(_user, "lifecycle_toggle", str(rid), f"enabled={not rule['enabled']}")
        return RedirectResponse(f"/admin/lifecycle/{rid}", status_code=303)

    @app.post("/admin/lifecycle/{rid}/delete")
    async def lifecycle_delete(rid: int, _user: str = Depends(_check_auth)) -> RedirectResponse:
        await credits_db.delete_lifecycle_rule(rid)
        await credits_db.audit_log(_user, "lifecycle_delete", str(rid))
        return RedirectResponse("/admin/lifecycle", status_code=303)

    # ── Audit log ─────────────────────────────────────────────────────

    @app.get("/admin/audit", response_class=HTMLResponse)
    async def audit_view(request: Request, _user: str = Depends(_check_auth)) -> str:
        page = _query_int(request, "page", default=1, min_value=1, max_value=10_000)
        per_page = 100
        rows = await credits_db.get_audit_log(limit=per_page, offset=(page - 1) * per_page)
        tr = "".join(
            f"<tr><td>{r['id']}</td><td>{html_mod.escape(r['admin_user'])}</td>"
            f"<td>{html_mod.escape(r['action'])}</td>"
            f"<td>{html_mod.escape(r['target'])}</td>"
            f"<td>{html_mod.escape(r['details'])}</td>"
            f"<td>{r['created_at']}</td></tr>"
            for r in rows
        )
        body = f"""
        <div class="card">
          <div class="table-wrap"><table>
            <tr><th>#</th><th>Admin</th><th>Action</th><th>Target</th><th>Details</th><th>At</th></tr>
            {tr or '<tr><td colspan=6>Пусто</td></tr>'}
          </table></div>
        </div>
        """
        return _page("Admin audit log", body)

    return app


async def start_admin_panel(
    credits_db: "CreditsDB",
    state_store: "StateStore",
    settings: "Settings",
    tbank_client: "TBankClient | None" = None,
    bot_ref: "list | None" = None,
    bot_app: "Any | None" = None,
) -> None:
    """Run the admin panel as an async background task."""
    # Seed default lifecycle rules (idempotent — inserts missing, refreshes
    # pristine ones with current spec, drops obsolete pristine names). Failures
    # shouldn't block admin panel startup.
    try:
        result = await credits_db.seed_default_lifecycle_rules(
            _DEFAULT_LIFECYCLE_RULES,
            legacy_names_to_drop=_LEGACY_LIFECYCLE_NAMES_TO_DROP,
        )
        if any(result.values()):
            log.info(
                "lifecycle seed: %d inserted, %d updated, %d dropped",
                result.get("inserted", 0), result.get("updated", 0), result.get("dropped", 0),
            )
    except Exception as e:
        log.warning("seed_default_lifecycle_rules failed: %s", e)

    app = build_app(credits_db, state_store, settings, tbank_client, bot_ref, bot_app)
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=settings.admin_panel_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    log.info("admin panel starting on port %s", settings.admin_panel_port)
    await server.serve()
