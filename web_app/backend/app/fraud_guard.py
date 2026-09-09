"""Анти-фрод: один аккаунт TikTok — один бесплатный лимит.

Правило владельца: подключение аккаунта TikTok открывает безлимит в рамках трека, поэтому
аккаунт TikTok — это вторая ступень проверки «человек настоящий». Если подключаемый аккаунт
TikTok уже светился в сервисе на ДРУГОМ аккаунте, значит один человек держит несколько
аккаунтов и получает бесплатный доступ повторно. Последствие — бан всех аккаунтов этого
человека, включая тот, где больше ничего не нарушено: нарушение состоит именно в наличии
нескольких аккаунтов.

Как определяется «все аккаунты этого человека»: по общим аккаунтам TikTok. Строим замыкание
по графу «юзер — open_id — юзер» (таблица `tiktok_account_usage`), то есть если A и B делили
один аккаунт TikTok, а B и C — другой, банятся все трое. Это единственная связь, которая у
нас есть и которую человек создал сам.

История использования НЕ удаляется вместе с аккаунтом: иначе правило обходилось бы
удалением своего аккаунта перед повторной регистрацией. В таблице лежит только
идентификатор аккаунта TikTok, без содержимого профиля (политика, раздел 6).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any, Iterable

from . import auth_store, db

# Причина бана. Строка уезжает на фронт и выбирает текст экрана блокировки.
BAN_TIKTOK_REUSE = "tiktok_reuse"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------- реестр TikTok

def record_usage(open_id: str, user_id: str) -> None:
    """Отметить, что этот аккаунт в сервисе подключал этот аккаунт TikTok."""
    if not open_id or not user_id:
        return
    now = _now_iso()
    db.migrate()
    with db.transaction() as cursor:
        # Обновляем только last_seen_at: дата ПЕРВОГО подключения — доказательство,
        # и общий db.upsert перетёр бы её (он обновляет все неключевые колонки).
        cursor.execute(
            db.sql("INSERT INTO tiktok_account_usage (open_id, user_id, first_seen_at, last_seen_at) "
                   "VALUES (%s,%s,%s,%s) ON CONFLICT (open_id, user_id) "
                   "DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at"),
            (open_id, user_id, now, now),
        )


def users_of(open_id: str) -> set[str]:
    """Все аккаунты сервиса, которые когда-либо подключали этот аккаунт TikTok."""
    if not open_id:
        return set()
    db.migrate()
    with db.read() as cursor:
        cursor.execute(db.sql("SELECT user_id FROM tiktok_account_usage WHERE open_id = %s"), (open_id,))
        return {row[0] for row in cursor.fetchall()}


def accounts_of(user_id: str) -> set[str]:
    """Все аккаунты TikTok, которые подключал этот аккаунт сервиса."""
    if not user_id:
        return set()
    db.migrate()
    with db.read() as cursor:
        cursor.execute(db.sql("SELECT open_id FROM tiktok_account_usage WHERE user_id = %s"), (user_id,))
        return {row[0] for row in cursor.fetchall()}


def ring(user_ids: Iterable[str], open_ids: Iterable[str] = ()) -> set[str]:
    """Замыкание «аккаунты одного человека»: обход графа юзер ↔ open_id в ширину."""
    users: set[str] = {uid for uid in user_ids if uid}
    seen_accounts: set[str] = set()
    pending_accounts: set[str] = {oid for oid in open_ids if oid}
    pending_users = set(users)
    while pending_users or pending_accounts:
        for user_id in list(pending_users):
            pending_accounts |= accounts_of(user_id) - seen_accounts
        pending_users.clear()
        for open_id in list(pending_accounts):
            seen_accounts.add(open_id)
            fresh = users_of(open_id) - users
            users |= fresh
            pending_users |= fresh
        pending_accounts.clear()
    return users


# ------------------------------------------------------------------------ бан

def ban_status(user_id: str | None) -> dict[str, Any] | None:
    """Данные бана для аккаунта или None. Читаем из реестра личностей (app_users)."""
    if not user_id:
        return None
    user = auth_store.user_by_id(user_id)
    if not user or not user.get("banned"):
        return None
    return {
        "banned": True,
        "reason": user.get("banReason") or BAN_TIKTOK_REUSE,
        "bannedAt": user.get("bannedAt"),
    }


def ban_users(user_ids: Iterable[str], reason: str) -> list[str]:
    """Проставить бан списку аккаунтов и сбросить реестр. Возвращает реально забаненных."""
    from . import persistence

    stamped: list[str] = []
    now = _now_iso()
    for user_id in user_ids:
        user = auth_store.user_by_id(user_id)
        if user is None or user.get("banned"):
            continue
        user["banned"] = True
        user["banReason"] = reason
        user["bannedAt"] = now
        stamped.append(user_id)
    if stamped:
        persistence.save_users()
    return stamped


def unban_users(user_ids: Iterable[str]) -> list[str]:
    """Снять бан (разбор обращения в поддержку, ошибочное срабатывание, dev-проверка).

    Историю использования аккаунтов TikTok при этом НЕ чистим: она доказательство, а не
    наказание. Если человеку вернули доступ, а связка осталась — повторное подключение того
    же аккаунта TikTok забанит снова, поэтому снимать бан нужно вместе с решением, что
    делать с самим аккаунтом TikTok.
    """
    from . import persistence

    lifted: list[str] = []
    for user_id in user_ids:
        user = auth_store.user_by_id(user_id)
        if user is None or not user.get("banned"):
            continue
        for field in ("banned", "banReason", "bannedAt"):
            user.pop(field, None)
        lifted.append(user_id)
    if lifted:
        persistence.save_users()
    return lifted


def register_connection(open_id: str, user_id: str) -> dict[str, Any] | None:
    """Проверить подключаемый аккаунт TikTok и записать использование.

    Возвращает None, если всё чисто. Если аккаунт TikTok уже использовался другим
    аккаунтом сервиса — банит всё кольцо и возвращает данные бана: вызывающий не должен
    подключать TikTok и обязан увести человека на экран блокировки.

    Падение БД не должно превращаться в «пропустить проверку молча»: пишем в stderr и
    отказываем в подключении (fail-closed) — так же, как гейт готовности к деплою.
    """
    if not open_id or not user_id:
        return None
    try:
        others = users_of(open_id) - {user_id}
        # использование пишем всегда, включая попытку: это и есть доказательство
        record_usage(open_id, user_id)
        if not others:
            return None
        family = ring({user_id, *others}, {open_id})
        ban_users(family, BAN_TIKTOK_REUSE)
        _revoke_tokens(family)
        return {
            "banned": True,
            "reason": BAN_TIKTOK_REUSE,
            "bannedAt": _now_iso(),
            "accounts": sorted(family),
        }
    except Exception as exc:  # noqa: BLE001
        print(f"[fraud_guard] register_connection failed: {exc}", file=sys.stderr)
        return {"banned": False, "reason": "guard_unavailable", "bannedAt": None, "accounts": []}


def _revoke_tokens(user_ids: Iterable[str]) -> None:
    """Забаненному TikTok не нужен: токены удаляем, чтобы их не обновлял планировщик."""
    from . import tiktok_token_store

    for user_id in user_ids:
        try:
            tiktok_token_store.delete(user_id)
        except Exception as exc:  # noqa: BLE001 — бан важнее чистки
            print(f"[fraud_guard] token cleanup failed for {user_id}: {exc}", file=sys.stderr)
