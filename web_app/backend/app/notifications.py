"""Durable web Telegram delivery; unique event keys survive polling/restarts.

Delivery is at least once: Telegram has no idempotency key, so a lost response
may cause a duplicate on a later retry. Failed events stay visible in the DB.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from . import db, telegram_bot

log = logging.getLogger(__name__)


def enqueue(key: str, *, chat_id: object, text: str, markup: dict | None = None, manager: bool = False,
            user_route: bool = False) -> None:
    """`user_route=True` — бота выбираем на доставке по пометке аккаунта
    (`auth_store.notify_bot_for_chat`): пометка может появиться позже постановки."""
    payload = {"chat_id": chat_id, "text": text, "markup": markup, "manager": manager, "user_route": user_route}
    with db.transaction() as cursor:
        cursor.execute(db.sql(
            "INSERT INTO notification_outbox (event_key, payload) VALUES (%s, %s) "
            "ON CONFLICT (event_key) DO NOTHING"
        ), (key, db.json_param(payload)))


def manager_event(key: str, text: str) -> None:
    # Routing is resolved at delivery so a configuration repair can drain the
    # existing queue without rewriting events or losing them.
    enqueue(key, chat_id=None, text=text, manager=True)


def deliver_pending() -> None:
    now = time.time()
    with db.read() as cursor:
        cursor.execute(db.sql(
            "SELECT event_key, payload, attempts FROM notification_outbox "
            "WHERE delivered_at IS NULL AND next_attempt <= %s ORDER BY next_attempt, event_key LIMIT 20"
        ), (now,))
        rows = cursor.fetchall()
    for key, raw, attempts in rows:
        payload: dict[str, Any] = db.json_value(raw)
        manager = bool(payload.get("manager"))
        chat_id = os.getenv("WEB_MANAGER_CHAT_ID", "").strip() if manager else payload["chat_id"]
        via = "auth"
        if not manager and payload.get("user_route"):
            from . import auth_store

            via = auth_store.notify_bot_for_chat(chat_id)
        ok = bool(chat_id) and telegram_bot._send(
            chat_id, payload["text"], payload.get("markup"), manager=manager, via=via
        )
        delay = min(900, 30 * 2 ** min(int(attempts), 5))
        with db.transaction() as cursor:
            cursor.execute(db.sql(
                "UPDATE notification_outbox SET attempts = attempts + 1, next_attempt = %s, "
                "delivered_at = %s, last_error = %s WHERE event_key = %s"
            ), (now + delay, time.time() if ok else None, "" if ok else "Telegram delivery failed; see API logs/routing configuration", key))
        if not ok:
            log.error("notification_pending event=%s attempt=%s retry_in=%ss", key, attempts + 1, delay)


def queue_job(job: dict[str, Any], *, unlimited_offer: bool = False) -> None:
    """`unlimited_offer` — в итоговом сообщении вторая кнопка «Оценить и получить
    безлимит». Решает вызывающий (production_monitor) по воронке: здесь поток без
    event loop, а репозиторий воронки асинхронный."""
    from . import auth_store

    chat_id = auth_store.chat_id_for_user(job.get("userId") or "")
    project_id = job.get("projectId") or ""
    videos = job.get("videos") or []
    if not chat_id:
        raise RuntimeError(f"notification owner has no Telegram chat: job={job['id']}")
    markup = telegram_bot._batch_button(telegram_bot.app_url(), project_id)
    for index, video in enumerate(videos, 1):
        if index <= telegram_bot.NOTIFY_LIMIT and video.get("status") == "COMPLETED":
            enqueue(f"job:{job['id']}:video:{video['id']}", chat_id=chat_id,
                    text=f"Ролик {index} из {len(videos)} готов", markup=markup, user_route=True)
    if job.get("status") in {"COMPLETED", "FAILED"}:
        completed = sum(v.get("status") == "COMPLETED" for v in videos)
        text = (f"Батч готов: {completed} роликов. Можно открыть их на сайте."
                if job["status"] == "COMPLETED" else
                f"Генерация остановилась. Готово {completed} из {len(videos)} роликов. Подробности — на сайте.")
        # Частично упавший батч — тоже повод оценить готовые ролики.
        terminal_markup = (
            telegram_bot._batch_button(telegram_bot.app_url(), project_id, unlimited_offer=True)
            if unlimited_offer and completed
            else markup
        )
        enqueue(f"job:{job['id']}:terminal", chat_id=chat_id, text=text, markup=terminal_markup, user_route=True)
        if job["status"] == "FAILED":
            error = next((str(v.get("error")) for v in videos if v.get("error")), "unknown")
            contact = auth_store.telegram_contact_for_user(job.get("userId") or "")
            manager_event(f"job:{job['id']}:failed", f"Ошибка генерации на сайте\nПользователь: {contact}\n"
                          f"Job: {job['id']}\nГотово: {completed}/{len(videos)}\n{error[:1500]}")
