"""Реальный реестр пользователей и токены TG-верификации (passwordless-модель).

Идентичность = Telegram: регистрация/логин выдают одноразовый токен, пользователь
подтверждает его в боте (`/start <token>`), после чего аккаунт верифицирован и залогинен.
Пока нет БД — персист в JSON (`backend/data/users.json`), чтобы аккаунты пережили рестарт.
Токены живут только в памяти (короткий TTL).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_USERS_FILE = _DATA_DIR / "users.json"
_lock = threading.Lock()

TOKEN_TTL_SEC = 10 * 60

# Псевдо-chat_id для дев-фолбэка (бот не настроен). Именно строка, а не None: ключ реестра
# собирается как `tg:<chat_id>`, а `get_user` приводит ключ к нижнему регистру — из «tg:None»
# получалось «tg:none», аккаунт не находился, и подтверждение в деве не срабатывало никогда.
DEV_CHAT_ID = "dev"

# email(lower) -> {id,email,name,surname,tgVerified,tgChatId,createdAt}
USERS: dict[str, dict[str, Any]] = {}
# token -> {token,email,purpose,verified,chatId,createdAt,polls}
TOKENS: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> None:
    """Первичное наполнение из JSON.

    Основное хранилище теперь БД (`app_users`), и `persistence.load_all()` перетирает
    этот словарь загруженным из неё. Файл остаётся источником для разового переноса
    аккаунтов, заведённых до появления БД.
    """
    global USERS
    try:
        USERS = json.loads(_USERS_FILE.read_text(encoding="utf-8")) if _USERS_FILE.exists() else {}
    except Exception:
        USERS = {}


def _save() -> None:
    """Сохранить реестр. Импорт локальный: persistence сам импортирует auth_store."""
    from . import persistence

    persistence.save_users()


_load()


def get_user(email: str) -> dict[str, Any] | None:
    key = (email or "").strip().lower()
    # tg:<chat_id> — ключ аккаунта, заведённого входом через Telegram (email ещё не задан)
    return USERS.get(key)


def create_user(email: str, name: str = "", surname: str = "") -> dict[str, Any]:
    key = (email or "").strip().lower()
    user = {
        "id": f"user_{uuid4().hex[:8]}",
        "email": key,
        "name": name or "",
        "surname": surname or "",
        "tgVerified": False,
        "tgChatId": None,
        "createdAt": _now_iso(),
    }
    with _lock:
        USERS[key] = user
        _save()
    return user


def issue_token(email: str, purpose: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    token = f"verify_{uuid4().hex[:12]}"
    rec = {
        "token": token,
        "email": (email or "").strip().lower(),
        "purpose": purpose,  # 'register' | 'login' | 'telegram'
        # Имя/фамилия с формы регистрации: они приоритетнее того, что отдаёт Telegram,
        # и подставляются при создании аккаунта в confirm_token.
        "profile": profile or {},
        "verified": False,
        "chatId": None,
        "createdAt": time.time(),
        "polls": 0,
    }
    TOKENS[token] = rec
    _save_token(rec)
    return rec


def token_status(token: str) -> dict[str, Any] | None:
    """Токен из памяти, а если его там нет — из БД.

    Второй путь обязателен: бот и веб могут жить в разных процессах (несколько воркеров,
    рестарт между «получил ссылку» и «нажал /start»). Пока токены были только в памяти,
    подтверждение в одном процессе не видел другой, и пользователю приходилось жать
    /start по нескольку раз, пока не попадёт в «тот самый» процесс.
    """
    rec = TOKENS.get(token)
    if rec is not None:
        stored = _load_token(token)
        # verified мог проставить ДРУГОЙ процесс — подтягиваем его к своей копии
        if stored and stored.get("verified") and not rec.get("verified"):
            rec.update({"verified": True, "chatId": stored.get("chatId"), "email": stored.get("email") or rec["email"]})
        # то же и для «аккаунта нет»: отметил бот, а отвечает на опрос веб-процесс
        if stored and stored.get("noAccount"):
            rec["noAccount"] = True
        return rec
    rec = _load_token(token)
    if rec is not None:
        TOKENS[token] = rec
    return rec


# --- персист токенов -------------------------------------------------------
# Импорт db локальный во всех функциях: auth_store поднимается очень рано, а падение
# на БД не должно ронять вход — при недоступной БД остаёмся на памяти, как раньше.

def _save_token(rec: dict[str, Any]) -> None:
    try:
        from . import db

        db.migrate()
        with _lock, db.transaction() as cursor:
            cursor.execute(
                db.upsert("auth_tokens", ["token"],
                          ["token", "email", "purpose", "profile", "verified", "chat_id", "polls", "created_at"]),
                (rec["token"], rec["email"], rec["purpose"], db.json_param(rec.get("profile") or {}),
                 bool(rec["verified"]), None if rec.get("chatId") is None else str(rec["chatId"]),
                 int(rec.get("polls") or 0), float(rec["createdAt"])),
            )
    except Exception:  # noqa: BLE001 — БД недоступна: вход продолжает работать на памяти
        pass


def _load_token(token: str) -> dict[str, Any] | None:
    try:
        from . import db

        db.migrate()
        with db.read() as cursor:
            cursor.execute(
                db.sql("SELECT token, email, purpose, profile, verified, chat_id, polls, created_at "
                       "FROM auth_tokens WHERE token = %s"),
                (token,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        stored = db.json_value(row[3]) or {}
        return {
            "token": row[0], "email": row[1], "purpose": row[2],
            "profile": stored, "verified": bool(row[4]),
            # флаг «аккаунта нет» живёт внутри profile: отдельная колонка ради одного булева
            # значения не стоит миграции, а прочитать его должен и другой процесс
            "noAccount": bool(stored.get("__noAccount")),
            "chatId": row[5], "polls": int(row[6] or 0), "createdAt": float(row[7]),
        }
    except Exception:  # noqa: BLE001
        return None


def purge_expired_tokens() -> None:
    """Подчистить протухшие токены — иначе таблица растёт вечно."""
    try:
        from . import db

        db.migrate()
        with _lock, db.transaction() as cursor:
            cursor.execute(db.sql("DELETE FROM auth_tokens WHERE created_at < %s"), (time.time() - TOKEN_TTL_SEC,))
    except Exception:  # noqa: BLE001
        pass


def chat_id_for_user(user_id: str) -> Any | None:
    """chat_id по id аккаунта — чтобы воркер мог написать владельцу джоба в Telegram."""
    user = next((u for u in USERS.values() if u.get("id") == user_id), None)
    return (user or {}).get("tgChatId")


def get_user_by_chat(chat_id: Any) -> dict[str, Any] | None:
    if chat_id is None:
        return None
    return next((u for u in USERS.values() if u.get("tgChatId") == chat_id), None)


def create_user_from_telegram(chat_id: Any, profile: dict[str, Any]) -> dict[str, Any]:
    """Аккаунт для входа «через Telegram»: email тут ещё неизвестен, личность = chat_id."""
    username = (profile.get("username") or "").strip()
    key = f"tg:{chat_id}"
    user = {
        "id": f"user_{uuid4().hex[:8]}",
        "email": "",
        # Пустое имя — валидное состояние: фронт по нему покажет обязательный шаг
        # «представься» (ProfileSetupGate). Подставлять «Артист» нельзя — юзер
        # останется с чужим именем в договоре и подписи роликов.
        "name": profile.get("name") or "",
        "surname": profile.get("surname") or "",
        "artistNick": username or None,
        "authProvider": "telegram",
        "tgVerified": True,
        "tgChatId": chat_id,
        "createdAt": _now_iso(),
    }
    with _lock:
        USERS[key] = user
        _save()
    return user


def user_by_id(user_id: str) -> dict[str, Any] | None:
    return next((u for u in USERS.values() if u.get("id") == user_id), None)


def find_user_by_google(email: str) -> dict[str, Any] | None:
    """Аккаунт по почте Google: сам ключ реестра либо ПРИВЯЗАННАЯ почта.

    Второй путь и есть связывание: телеграм-аккаунт лежит под ключом `tg:<chat_id>`,
    и почта у него хранится полем `googleEmail`. Заводить второй ключ на того же юзера
    нельзя — в БД на `user_id` стоит UNIQUE, и запись просто не прошла бы.
    """
    key = (email or "").strip().lower()
    if not key:
        return None
    direct = USERS.get(key)
    if direct is not None:
        return direct
    return next((u for u in USERS.values() if (u.get("googleEmail") or "").lower() == key), None)


def link_google(user_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Привязать Google к УЖЕ существующему аккаунту (кнопка в профиле).

    Если этой почтой уже пользуется другой аккаунт — отказываем: молча склеивать два
    аккаунта нельзя, у каждого свои проекты и своя подписка.
    """
    user = user_by_id(user_id)
    if user is None:
        raise ValueError("user_not_found")
    email = (profile.get("email") or "").strip().lower()
    if not email:
        raise ValueError("google profile without email")
    owner = find_user_by_google(email)
    if owner is not None and owner.get("id") != user_id:
        raise ValueError("google_taken")
    user["googleEmail"] = email
    user["googleSub"] = profile.get("googleSub")
    if not user.get("avatarUrl"):
        user["avatarUrl"] = profile.get("avatarUrl")
    _save()
    return user


def unlink_google(user_id: str) -> dict[str, Any]:
    """Отвязать Google. Аккаунтам, ЗАВЕДЁННЫМ через Google, отвязка запрещена —
    иначе человек останется без единственного способа войти."""
    user = user_by_id(user_id)
    if user is None:
        raise ValueError("user_not_found")
    if user.get("authProvider") == "google" and not user.get("tgChatId"):
        raise ValueError("last_provider")
    user.pop("googleEmail", None)
    user.pop("googleSub", None)
    _save()
    return user


def get_or_create_google_user(profile: dict[str, Any]) -> dict[str, Any]:
    """Аккаунт по подтверждённой почте Google: находим или заводим.

    Ключ в реестре — почта, как и было задумано под email-аккаунты. Аккаунты, заведённые
    через Telegram, лежат под ключом `tg:<chat_id>` и почты не имеют, поэтому один и тот же
    человек, зашедший обоими способами, получит ДВА аккаунта. Связывание способов входа —
    отдельная задача (см. README), молча склеивать по имени нельзя.
    """
    key = (profile.get("email") or "").strip().lower()
    if not key:
        raise ValueError("google profile without email")
    # Сначала ищем среди ПРИВЯЗАННЫХ: телеграм-аккаунт с подключённым Google должен
    # пускать по этой же почте, а не заводить второй аккаунт с нуля.
    user = find_user_by_google(key)
    if user is None:
        user = {
            "id": f"user_{uuid4().hex[:8]}",
            "email": key,
            "name": profile.get("name") or "",
            "surname": profile.get("surname") or "",
            "artistNick": None,
            "avatarUrl": profile.get("avatarUrl"),
            "authProvider": "google",
            "googleSub": profile.get("googleSub"),
            # Telegram он не подтверждал — уведомления о готовых роликах ему не уйдут,
            # пока он не привяжет бота.
            "tgVerified": False,
            "tgChatId": None,
            "createdAt": _now_iso(),
        }
        with _lock:
            USERS[key] = user
    else:
        # Профиль в Google мог поменяться — подтягиваем то, чего у нас ещё нет
        user.setdefault("authProvider", "google")
        user["googleSub"] = profile.get("googleSub") or user.get("googleSub")
        if not user.get("name"):
            user["name"] = profile.get("name") or ""
        if not user.get("surname"):
            user["surname"] = profile.get("surname") or ""
        if not user.get("avatarUrl"):
            user["avatarUrl"] = profile.get("avatarUrl")
    _save()
    return user


def confirm_token(token: str, chat_id: Any = None, profile: dict[str, Any] | None = None,
                  allow_create: bool = True) -> str:
    """Бот вызывает при `/start <token>`: помечает токен и верифицирует пользователя.

    Возвращает исход: `ok` | `expired` | `no_account`.

    Токен без email — это вход «через Telegram»: аккаунт ищем по chat_id. Заводить новый
    можно ТОЛЬКО когда человек пришёл с регистрации (`purpose != 'login'`): раньше кнопка
    «Войти» молча создавала аккаунт, и человек, у которого его ещё не было, возвращался из
    бота на обязательный экран «представься» — это читалось как сбой, а не как регистрация.

    `allow_create` оставлен для дев-фолбэка без бота: там chat_id вообще нет, и найти
    существующий аккаунт по нему невозможно — иначе локально не залогиниться.
    """
    # через token_status, а не TOKENS: токен мог быть выдан другим процессом
    rec = token_status(token)
    if not rec or time.time() - rec["createdAt"] > TOKEN_TTL_SEC:
        return "expired"

    if not rec["email"]:
        user = get_user_by_chat(chat_id)
        if not user and rec.get("purpose") == "login" and not allow_create:
            # НЕ верифицируем: вход не состоялся, фронт по этому флагу предложит регистрацию.
            # Флаг кладём и в profile — так его увидит процесс, который опрашивает статус.
            rec["noAccount"] = True
            rec.setdefault("profile", {})["__noAccount"] = True
            _save_token(rec)
            return "no_account"
        rec["verified"] = True
        rec["chatId"] = chat_id
        # Форма регистрации (если была) важнее профиля Telegram: юзер вписал так, как хочет
        form = {key: value for key, value in (rec.get("profile") or {}).items()
                if key in ("name", "surname") and value}
        if not user:
            user = create_user_from_telegram(chat_id, {**(profile or {}), **form})
        elif form:
            # Аккаунт уже был, но человек только что представился на форме регистрации —
            # значит имя нужно обновить. Раньше форма молча игнорировалась, и в профиле
            # навсегда оставалось имя из Telegram.
            user.update(form)
            with _lock:
                _save()
        rec["email"] = user["email"] or f"tg:{chat_id}"
        _save_token(rec)
        return "ok"

    rec["verified"] = True
    rec["chatId"] = chat_id
    _save_token(rec)
    user = USERS.get(rec["email"])
    if user:
        user["tgVerified"] = True
        if chat_id is not None:
            user["tgChatId"] = chat_id
        with _lock:
            _save()
    return "ok"
