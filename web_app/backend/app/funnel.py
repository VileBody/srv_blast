"""Воронка сайта после генерации (docs/BOT_TO_WEB_FLOW.md, разделы 4–6).

Квиз «как делаешь контент», оценки роликов, ценностные действия (подписка на ТГК,
сообщение менеджеру) и бесплатный «безлимит на трек» с квотами.

Данные — в общей с ботом Postgres (credits_db): квиз пишется в ту же
`survey_responses`, что и в боте, поэтому человек проходит его один раз на оба
канала. Вопросы, тексты-мостики и правила квоты — тоже общие
(`services.tg_bot_public.marketing_texts` / `track_unlimited`).

В mock-режиме (локальный запуск без БД) всё живёт в памяти процесса — чтобы флоу
можно было прокликать на превью. Проверка подписки на канал там всегда успешна.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol
from urllib.parse import quote

from services.tg_bot_public import marketing_texts as mt
from services.tg_bot_public import track_unlimited as tu

from . import telegram_bot
from .runtime import SETTINGS as RUNTIME

log = logging.getLogger(__name__)

ACTION_CHANNEL = "channel_subscribed"
ACTION_MANAGER = "manager_contacted"
# Действия, которые требует безлимит. Сообщение менеджеру — больше НЕ условие: его
# засчитывал голый POST без проверки (скрипт открывал безлимит). Его роль «человек
# вовлёкся» теперь играет оценка ролика или пройденный опрос — их сервер проверяет
# по своим данным (video_ratings / survey_responses). Ссылка на менеджера осталась
# необязательной; ACTION_MANAGER пишется только для аналитики.
UNLOCK_ACTIONS = (ACTION_CHANNEL,)
# Все действия, которые видит фронт в state.actions (старые бандлы ждут оба ключа).
KNOWN_ACTIONS = (ACTION_CHANNEL, ACTION_MANAGER)

CHANNEL_CHECK_UNAVAILABLE_MESSAGE = "Не смогли проверить подписку, попробуй через минуту."
# Сколько раз проверка подписки на сайте сломалась (и человека не пустили), по месту.
# Счётчик процесса: пишется в каждый error-лог сбоя (алерт в Loki по ключу лога).
CHANNEL_CHECK_FAILURES: "collections.Counter[str]" = collections.Counter()

MANAGER_USERNAME = os.getenv("WEB_MANAGER_USERNAME", "impulsemanage").strip().lstrip("@")
SUBSCRIPTION_CHANNEL = os.getenv("SUBSCRIPTION_CHANNEL", "@impulsemarketing").strip()
PUBLIC_BOT_USERNAME = os.getenv("WEB_PUBLIC_BOT_USERNAME", "blast808bot").strip().lstrip("@")
# Прямая ссылка на методичку. Пусто — методичку присылает публичный бот документом.
METHODOLOGY_URL = os.getenv("METHODOLOGY_URL", "").strip()


class FunnelError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        # доп. поля в detail ответа (например, чего не хватает для безлимита)
        self.extra = dict(extra or {})


class _Repo(Protocol):
    async def has_paid(self, tg_id: int) -> bool: ...
    async def get_survey_response(self, tg_id: int) -> dict[str, Any] | None: ...
    async def save_survey_answer(self, tg_id: int, **kw: Any) -> None: ...
    async def save_video_rating(self, tg_id: int, **kw: Any) -> None: ...
    async def list_video_ratings(self, tg_id: int, video_ids: list[str]) -> dict[str, dict[str, Any]]: ...
    async def has_video_rating(self, tg_id: int) -> bool: ...
    async def mark_funnel_action(self, tg_id: int, action: str, detail: str = "") -> bool: ...
    async def funnel_actions(self, tg_id: int) -> dict[str, datetime]: ...
    async def get_track_unlimited(self, tg_id: int) -> dict[str, Any] | None: ...
    async def unlock_track_unlimited(self, tg_id: int, audio_hash: str) -> dict[str, Any]: ...
    async def list_track_batches(self, tg_id: int, audio_hash: str) -> list[dict[str, Any]]: ...
    async def record_track_batch(self, **kw: Any) -> None: ...
    async def claim_track_batch(self, **kw: Any) -> str | None: ...
    async def release_track_batch(self, job_id: str, videos: int) -> None: ...
    async def drop_track_batch(self, job_id: str) -> None: ...
    async def has_track_tripwire(self, tg_id: int, audio_hash: str) -> bool: ...


class MemoryRepo:
    """Тот же контракт, что у credits_db, — в памяти (mock-режим и тесты)."""

    def __init__(self) -> None:
        self.paid: set[int] = set()
        self.surveys: dict[int, dict[str, Any]] = {}
        self.ratings: dict[tuple[int, str], dict[str, Any]] = {}
        self.rating_jobs: dict[tuple[int, str], str] = {}
        self.actions: dict[int, dict[str, datetime]] = {}
        self.unlimited: dict[int, dict[str, Any]] = {}
        self.batches: list[dict[str, Any]] = []
        self.tripwire: set[tuple[int, str]] = set()
        # Замок «проверить квоту и записать батч» на человека — то же, что
        # pg_advisory_xact_lock в credits_db.claim_track_batch.
        self.locks: dict[int, asyncio.Lock] = {}

    def _lock(self, tg_id: int) -> asyncio.Lock:
        return self.locks.setdefault(int(tg_id), asyncio.Lock())

    @contextlib.asynccontextmanager
    async def batch_lock(self, tg_id: int) -> AsyncIterator[None]:
        """Mock-сабмит: проверка квоты, заведение джоба и запись батча — под одним замком."""
        async with self._lock(tg_id):
            yield

    async def has_paid(self, tg_id: int) -> bool:
        return int(tg_id) in self.paid

    async def get_survey_response(self, tg_id: int) -> dict[str, Any] | None:
        return self.surveys.get(int(tg_id))

    async def save_survey_answer(self, tg_id: int, *, question_id: str, answer_id: str, answer_label: str,
                                 branch_q2: str = "", branch_q3: str = "", completed: bool = False) -> None:
        row = self.surveys.setdefault(int(tg_id), {"answers": {}, "branch_q2": "", "branch_q3": "", "completed_at": None})
        row["answers"][question_id] = {"id": answer_id, "label": answer_label}
        row["branch_q2"] = branch_q2 or row["branch_q2"]
        row["branch_q3"] = branch_q3 or row["branch_q3"]
        if completed and row["completed_at"] is None:
            row["completed_at"] = datetime.now(timezone.utc)

    async def save_video_rating(self, tg_id: int, *, video_id: str, score: int, job_id: str = "",
                                project_id: str = "", reasons: list[str] | None = None, comment: str = "",
                                source: str = "web") -> None:
        self.ratings[(int(tg_id), video_id)] = {"score": int(score), "reasons": list(reasons or []), "comment": comment}
        self.rating_jobs[(int(tg_id), video_id)] = str(job_id or "")

    async def list_video_ratings(self, tg_id: int, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {v: self.ratings[(int(tg_id), v)] for v in video_ids if (int(tg_id), v) in self.ratings}

    async def has_video_rating(self, tg_id: int) -> bool:
        return any(t == int(tg_id) and job for (t, _v), job in self.rating_jobs.items())

    async def mark_funnel_action(self, tg_id: int, action: str, detail: str = "") -> bool:
        acts = self.actions.setdefault(int(tg_id), {})
        if action in acts:
            return False
        acts[action] = datetime.now(timezone.utc)
        return True

    async def funnel_actions(self, tg_id: int) -> dict[str, datetime]:
        return dict(self.actions.get(int(tg_id), {}))

    async def get_track_unlimited(self, tg_id: int) -> dict[str, Any] | None:
        return self.unlimited.get(int(tg_id))

    async def unlock_track_unlimited(self, tg_id: int, audio_hash: str) -> dict[str, Any]:
        return self.unlimited.setdefault(int(tg_id), {
            "tg_id": int(tg_id), "audio_hash": audio_hash, "unlocked_at": datetime.now(timezone.utc),
            "tripwire_order_id": "", "tripwire_paid_at": None,
        })

    async def list_track_batches(self, tg_id: int, audio_hash: str) -> list[dict[str, Any]]:
        return [
            {**b, "videos": max(0, b["videos"] - b["released"])}
            for b in self.batches if b["tg_id"] == int(tg_id) and b["audio_hash"] == audio_hash
        ]

    async def record_track_batch(self, *, tg_id: int, audio_hash: str, job_id: str, videos: int, mode: str) -> None:
        if any(b["job_id"] == job_id for b in self.batches):
            return
        self.batches.append({"tg_id": int(tg_id), "audio_hash": audio_hash, "job_id": job_id, "videos": int(videos),
                             "released": 0, "mode": mode, "created_at": datetime.now(timezone.utc)})

    async def claim_track_batch(self, *, tg_id: int, audio_hash: str, job_id: str, videos: int,
                                admit: Callable[[bool, dict[str, Any] | None, list[dict[str, Any]]], bool]) -> str | None:
        async with self._lock(tg_id):
            known = next((b["mode"] for b in self.batches if b["job_id"] == job_id), None)
            if known is not None:
                return str(known)
            tripwire = (int(tg_id), audio_hash) in self.tripwire
            unl = self.unlimited.get(int(tg_id))
            rows = await self.list_track_batches(tg_id, audio_hash)
            # Между чтением и записью в БД — сетевой круг; здесь отдаём цикл, чтобы
            # гонка двух сабмитов воспроизводилась и в памяти (её держит замок).
            await asyncio.sleep(0)
            if not admit(tripwire, dict(unl) if unl else None, rows):
                return None
            await self.record_track_batch(tg_id=tg_id, audio_hash=audio_hash, job_id=job_id, videos=videos, mode="free")
            return "free"

    async def release_track_batch(self, job_id: str, videos: int) -> None:
        for b in self.batches:
            if b["job_id"] == job_id:
                b["released"] = min(b["videos"], b["released"] + max(0, int(videos)))

    async def drop_track_batch(self, job_id: str) -> None:
        self.batches = [b for b in self.batches if b["job_id"] != job_id]

    async def has_track_tripwire(self, tg_id: int, audio_hash: str) -> bool:
        return (int(tg_id), audio_hash) in self.tripwire


_MEMORY = MemoryRepo()


def repo() -> _Repo:
    if RUNTIME.backend == "production":
        from .billing_backend import get_billing

        return get_billing().db
    return _MEMORY


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ квиз

def web_bridge(branch: str) -> str:
    """Мостик для сайта: свой текст из marketing_texts (WEB_*). Ботовый свёрстан под
    Telegram (ручные переносы, длинные тире, стрелка-эмодзи на документ), а на сайте
    методичка стоит карточкой ниже и действуют правила копирайта сайта."""
    return mt.web_bridge_text_for_branch(branch)


def survey_questions() -> list[dict[str, Any]]:
    """Вопросы квиза в порядке и с ветвлением — те же, что у бота (тексты — сайта)."""
    return [
        {
            "id": q.id,
            "text": mt.WEB_SURVEY_QUESTION_TEXT.get(q.id, q.text),
            "options": [
                {"id": o.id, "label": mt.WEB_SURVEY_OPTION_LABEL.get((q.id, o.id), o.label)} for o in q.options
            ],
            "next": {**{o.id: q.next_by_answer.get(o.id, q.next_default) for o in q.options}},
        }
        for q in mt.SURVEY_QUESTIONS.values()
    ]


async def answer_survey(tg_id: int, question_id: str, answer_id: str) -> dict[str, Any]:
    question = mt.SURVEY_QUESTIONS.get(question_id)
    if question is None:
        raise FunnelError("survey_unknown_question", "Нет такого вопроса.", 422)
    option = next((o for o in question.options if o.id == answer_id), None)
    if option is None:
        raise FunnelError("survey_unknown_answer", "Нет такого ответа.", 422)
    nxt = question.next_by_answer.get(answer_id, question.next_default)
    branch_q2 = mt.SURVEY_Q2_BRANCH_BY_ANSWER.get(answer_id, "") if question_id == "q2" else ""
    branch_q3 = mt.SURVEY_Q3_BRANCH_BY_ANSWER.get(answer_id, "") if question_id == "q3" else ""
    await repo().save_survey_answer(
        int(tg_id),
        question_id=question_id,
        answer_id=answer_id,
        answer_label=option.label,
        branch_q2=branch_q2,
        branch_q3=branch_q3,
        completed=not nxt,
    )
    out: dict[str, Any] = {"next": nxt or None, "done": not nxt}
    if not nxt:
        out["branch"] = branch_q3
        out["bridge"] = web_bridge(branch_q3)
    return out


# ------------------------------------------------------------------ действия

def manager_code(tg_id: int) -> str:
    """Короткий код в сообщении менеджеру: по нему он видит, кто пишет."""
    return "B-" + hashlib.sha256(f"blast-manager:{int(tg_id)}".encode()).hexdigest()[:6].upper()


def manager_link(tg_id: int, track_title: str) -> str:
    text = f"Привет! Хочу безлимит на трек «{track_title}». Код: {manager_code(tg_id)}"
    return f"https://t.me/{MANAGER_USERNAME}?text={quote(text)}"


def channel_link() -> str:
    return f"https://t.me/{SUBSCRIPTION_CHANNEL.lstrip('@')}"


def _channel_check_failed(tg_id: int, where: str, reason: str) -> FunnelError:
    """Проверка подписки сломалась: fail-closed. Человека НЕ пускаем, но и не говорим
    «не подписан» — просим повторить; сбой виден error-логом со счётчиком."""
    CHANNEL_CHECK_FAILURES[where] += 1
    log.error(
        "web_channel_check_failed_blocked tg_id=%s where=%s reason=%s count=%s total=%s",
        tg_id, where, reason, CHANNEL_CHECK_FAILURES[where], sum(CHANNEL_CHECK_FAILURES.values()),
    )
    return FunnelError("channel_check_unavailable", CHANNEL_CHECK_UNAVAILABLE_MESSAGE, 503)


def check_channel_member(tg_id: int, *, where: str = "funnel") -> bool:
    """Подписан ли человек на канал — тем же публичным ботом, что проверял онбординг.

    True/False — ответ Telegram; любой сбой (нет токена, ошибка API, сеть) —
    FunnelError `channel_check_unavailable` (503, повторяемая), не False и не True."""
    if RUNTIME.backend != "production":
        return True
    token = os.getenv("WEB_PUBLIC_BOT_TOKEN", "").strip()
    if not token:
        raise _channel_check_failed(tg_id, where, "no_token")
    try:
        result = telegram_bot._api("getChatMember", {"chat_id": SUBSCRIPTION_CHANNEL, "user_id": int(tg_id)}, token=token)
    except Exception as exc:  # urllib кидает HTTPError на 4xx/5xx — это тоже «не проверили»
        raise _channel_check_failed(tg_id, where, f"{type(exc).__name__}: {exc}"[:300]) from exc
    if not result.get("ok"):
        raise _channel_check_failed(tg_id, where, str(result.get("description") or "not ok")[:300])
    return str((result.get("result") or {}).get("status") or "") in {"member", "administrator", "creator"}


async def require_channel_for_free(tg_id: int, audio_hash: str = "") -> None:
    """Гейт сабмита на сайте: бесплатный человек запускает генерацию только с подпиской
    на канал (бесплатные кредиты и безлимит на трек). Платящих не трогаем вовсе; купивший
    трипваер на этот трек тоже заплатил за него — его не гейтим.

    Проверка живая (getChatMember на каждый сабмит): отписаться после выдачи кредитов
    и генерить дальше нельзя. Успех заодно засчитывает шаг «подписка» в воронке."""
    r = repo()
    tg_id = int(tg_id)
    if await r.has_paid(tg_id):
        return
    if audio_hash and await r.has_track_tripwire(tg_id, audio_hash):
        return
    subscribed = await asyncio.to_thread(check_channel_member, tg_id, where="submit")
    if not subscribed:
        raise FunnelError(
            "channel_subscription_required",
            "Подпишись на канал, чтобы запускать бесплатные генерации.",
            403,
            {"channel": channel_link()},
        )
    await r.mark_funnel_action(tg_id, ACTION_CHANNEL)


def send_methodology(tg_id: int) -> dict[str, Any]:
    """Методичка: прямой ссылкой, если она задана, иначе — документом от публичного бота."""
    if METHODOLOGY_URL:
        return {"url": METHODOLOGY_URL, "sent": False}
    if RUNTIME.backend != "production":
        return {"url": None, "sent": True}
    token = os.getenv("WEB_PUBLIC_BOT_TOKEN", "").strip()
    if not token:
        raise FunnelError("methodology_unavailable", "Отправка методички не настроена.", 503)
    result = telegram_bot._api("sendDocument", {"chat_id": int(tg_id), "document": mt.METHODOLOGY_FILE_ID}, token=token)
    if result.get("ok"):
        return {"url": None, "sent": True}
    # Публичного бота человек не запускал — первым бот написать не может.
    return {"url": None, "sent": False, "botLink": f"https://t.me/{PUBLIC_BOT_USERNAME}"}


# ------------------------------------------------------------------ безлимит

def track_title(filename: str) -> str:
    """Название трека для людей: имя файла без расширения («Нет любви.mp3» → «Нет любви»)."""
    name = str(filename or "").strip()
    stem, dot, ext = name.rpartition(".")
    return stem if dot and stem and 1 <= len(ext) <= 5 else name


def _batches(rows: list[dict[str, Any]]) -> list[tu.TrackBatch]:
    return [tu.TrackBatch(r["created_at"], int(r["videos"]), str(r["mode"])) for r in rows]


def quota_view(q: tu.TrackQuota) -> dict[str, Any]:
    return {
        "allowed": q.allowed,
        "reason": q.reason,
        "maxVideos": q.max_videos,
        "batchCap": q.batch_cap,
        "availableAt": q.available_at.isoformat() if q.available_at else None,
        "tripwire": q.tripwire,
        "tripwirePriceRub": tu.TRIPWIRE_PRICE_RUB,
        "tripwireBatchCap": tu.TRIPWIRE_BATCH_CAP,
    }


def _quota_of(*, now: datetime, audio_hash: str, tripwire: bool, unl: dict[str, Any] | None,
              rows: list[dict[str, Any]]) -> tu.TrackQuota | None:
    """Квота трека по фактам: купленный трипваер на нём, бесплатный безлимит на нём или None.

    Трипваер живёт в track_tripwire (на любой трек); колонка
    track_unlimited.tripwire_paid_at не пишется и здесь не участвует."""
    if audio_hash and tripwire:
        return tu.evaluate(now=now, unlocked_at=now, tripwire=True, batches=[])
    if not unl or unl["audio_hash"] != audio_hash:
        return None
    return tu.evaluate(now=now, unlocked_at=unl["unlocked_at"], tripwire=False, batches=_batches(rows))


async def track_quota(tg_id: int, audio_hash: str) -> tu.TrackQuota | None:
    """Квота трека: купленный трипваер на нём, бесплатный безлимит на нём или None."""
    r = repo()
    tripwire = bool(audio_hash) and await r.has_track_tripwire(int(tg_id), audio_hash)
    unl = None if tripwire else await r.get_track_unlimited(int(tg_id))
    rows = await r.list_track_batches(int(tg_id), audio_hash) if unl and unl["audio_hash"] == audio_hash else []
    return _quota_of(now=now_utc(), audio_hash=audio_hash, tripwire=tripwire, unl=unl, rows=rows)


def _free_fits(q: tu.TrackQuota | None, videos: int) -> bool:
    return q is not None and q.allowed and videos <= q.max_videos


async def tripwire_offer(tg_id: int, quota: tu.TrackQuota | None) -> dict[str, Any] | None:
    """Окно предложения трипваера: открывается при первом РЕАЛЬНОМ упоре в лимит, живёт сутки.

    `quota` — квота, в которую человек только что упёрся (отказ сабмита, показ окна
    перезарядки): заблокированная — открывает окно; None — только прочитать.
    Отметка ставится на сервере (funnel_actions) — от неё же считает догон бота."""
    r = repo()
    if quota is not None and not quota.allowed and not quota.tripwire:
        await r.mark_funnel_action(int(tg_id), "tripwire_offer")
    opened = (await r.funnel_actions(int(tg_id))).get("tripwire_offer")
    if opened is None:
        return None
    expires = opened + tu.TRIPWIRE_OFFER_WINDOW
    if now_utc() >= expires:
        return None
    return {"expiresAt": expires.isoformat(), "priceRub": tu.TRIPWIRE_PRICE_RUB}


async def open_tripwire_offer(tg_id: int, audio_hash: str = "") -> dict[str, Any] | None:
    """Открыть окно трипваера, если человек на самом деле упёрся в лимит, и вернуть его.

    Упор — это: безлимит на трек сейчас на перезарядке или исчерпан за сутки; либо
    человек на другом треке (`audio_hash`), а безлимит уже открыт не на нём — экран
    «безлимит уже на другом треке» с трипваером. Трипваер — на любой трек, поэтому
    показ этого экрана и покупка с него открывают окно, а не отвечают «предложение
    закончилось». Платящим окно не открываем: воронка конверсионная."""
    r = repo()
    tg_id = int(tg_id)
    if await r.has_paid(tg_id):
        return await tripwire_offer(tg_id, None)
    unl = await r.get_track_unlimited(tg_id)
    if unl is None:
        return await tripwire_offer(tg_id, None)
    if audio_hash and audio_hash != unl["audio_hash"] and not await r.has_track_tripwire(tg_id, audio_hash):
        await r.mark_funnel_action(tg_id, "tripwire_offer", "other_track")
        return await tripwire_offer(tg_id, None)
    return await tripwire_offer(tg_id, await track_quota(tg_id, unl["audio_hash"]))


async def _used_since_unlock(tg_id: int, unl: dict[str, Any]) -> bool:
    """Был ли батч по треку уже ПОСЛЕ открытия безлимита. Без этого перезарядка от
    стартового батча (первые 5 роликов до открытия) открывала окно трипваера в сам
    момент открытия безлимита — человек ещё ни во что не упирался."""
    rows = await repo().list_track_batches(int(tg_id), unl["audio_hash"])
    return any(b["created_at"] >= unl["unlocked_at"] for b in rows)


async def state(tg_id: int, *, saved_tracks: list[dict[str, Any]]) -> dict[str, Any]:
    r = repo()
    tg_id = int(tg_id)
    survey = await r.get_survey_response(tg_id) or {}
    actions = await r.funnel_actions(tg_id)
    unl = await r.get_track_unlimited(tg_id)
    unlimited = None
    unl_quota = None
    if unl:
        track = next((t for t in saved_tracks if t.get("audioHash") == unl["audio_hash"]), None)
        q = unl_quota = await track_quota(tg_id, unl["audio_hash"])
        unlimited = {
            # Трек сверяется по хэшу: SavedTrack мог пропасть (или трек открыт из бота),
            # а id на сайте у одного трека бывает не один.
            "audioHash": unl["audio_hash"],
            "trackId": track["id"] if track else None,
            "trackTitle": track_title(track["filename"]) if track else None,
            "unlockedAt": unl["unlocked_at"].isoformat(),
            "tripwire": bool(q and q.tripwire),
            "quota": quota_view(q) if q else None,
        }
    return {
        "hasPaid": await r.has_paid(tg_id),
        "survey": {
            "answers": dict(survey.get("answers") or {}),
            "completed": survey.get("completed_at") is not None,
            "branch": str(survey.get("branch_q3") or ""),
            "bridge": web_bridge(str(survey.get("branch_q3") or "")) if survey.get("completed_at") else None,
        },
        "actions": {a: a in actions for a in KNOWN_ACTIONS},
        # Второе условие безлимита (вместо сообщения менеджеру): оценка ролика своего
        # батча ИЛИ пройденный опрос — по данным сервера, не по отметке фронта.
        "feedback": await _feedback_view(tg_id, survey),
        # Окно открывает только упор после реальной генерации по безлимиту (иначе — только
        # чтение): отказ сабмита и экран «другой трек» открывают его сами.
        "tripwireOffer": await tripwire_offer(
            tg_id, unl_quota if unl and unl_quota and await _used_since_unlock(tg_id, unl) else None
        ),
        "unlimited": unlimited,
        "links": {
            "channel": channel_link(),
            "manager": f"https://t.me/{MANAGER_USERNAME}",
            "managerCode": manager_code(tg_id),
            "bot": f"https://t.me/{PUBLIC_BOT_USERNAME}",
        },
        "rules": {
            "batchCap": tu.FREE_BATCH_CAP,
            "cooldownHours": int(tu.COOLDOWN.total_seconds() // 3600),
            "firstDayBatches": tu.FIRST_DAY_BATCHES,
            "dailyVideos": tu.DAILY_VIDEOS,
            "tripwirePriceRub": tu.TRIPWIRE_PRICE_RUB,
            "tripwireBatchCap": tu.TRIPWIRE_BATCH_CAP,
        },
        "questions": survey_questions(),
    }


async def _feedback_view(tg_id: int, survey: dict[str, Any] | None) -> dict[str, bool]:
    rated = await repo().has_video_rating(int(tg_id))
    surveyed = bool(survey) and survey.get("completed_at") is not None
    return {"rated": rated, "surveyCompleted": surveyed, "done": rated or surveyed}


async def unlock(tg_id: int, audio_hash: str) -> dict[str, Any]:
    """Открыть безлимит: подписка на канал + (оценка ролика ИЛИ пройденный опрос).
    Повторно на другой трек — отказ."""
    r = repo()
    tg_id = int(tg_id)
    # Уже открытый безлимит не отзываем и условий заново не спрашиваем: открывшие его по
    # старым правилам (подписка + менеджер) остаются с ним.
    existing = await r.get_track_unlimited(tg_id)
    if existing is None:
        # Воронка конверсионная: платящим безлимит не открываем (у них тариф).
        if await r.has_paid(tg_id):
            raise FunnelError("unlimited_paid", "Безлимит на трек открывается только на бесплатном тарифе.", 409)
        actions = await r.funnel_actions(tg_id)
        feedback = await _feedback_view(tg_id, await r.get_survey_response(tg_id))
        missing = [name for name, ok in (("channel", ACTION_CHANNEL in actions), ("feedback", feedback["done"])) if not ok]
        if "feedback" in missing:
            raise FunnelError(
                "unlock_feedback_missing", "Сначала оцени ролик или пройди короткий опрос.", 409, {"missing": missing},
            )
        if missing:
            raise FunnelError("unlock_channel_missing", "Сначала подпишись на канал.", 409, {"missing": missing})
    row = existing or await r.unlock_track_unlimited(tg_id, audio_hash)
    if row["audio_hash"] != audio_hash:
        raise FunnelError("unlimited_other_track", "Безлимит уже открыт на другом треке.", 409)
    return row


async def unlimited_offer_due(tg_id: int) -> bool:
    """Звать ли в «батч готов» за безлимитом: бесплатный, и безлимит ещё не открыт."""
    r = repo()
    if await r.has_paid(int(tg_id)):
        return False
    return await r.get_track_unlimited(int(tg_id)) is None


async def plan_generation(tg_id: int, audio_hash: str, videos: int, credits_left: int) -> tuple[str, tu.TrackQuota | None]:
    """Каким путём запускать батч: «free» (безлимит трека) или «credits».

    Безлимитный трек сначала тратит квоту; если её не хватает, а кредитов хватает —
    идём за кредиты. Не хватает ни того, ни другого — отказ с причиной для экрана
    перезарядки (FunnelError с quota в details)."""
    q = await track_quota(tg_id, audio_hash)
    if q is None:
        return "credits", None
    if q.allowed and videos <= q.max_videos:
        return "free", q
    if credits_left >= videos:
        return "credits", q
    if q.allowed:
        raise TrackLimitError("track_batch_cap", f"За раз — не больше {q.max_videos} роликов.", q)
    raise TrackLimitError(q.reason, "Лимит на трек пока исчерпан.", q)


class TrackLimitError(FunnelError):
    def __init__(self, code: str, message: str, quota: tu.TrackQuota) -> None:
        super().__init__(code, message, 402)
        self.quota = quota


class CreditsExhausted(FunnelError):
    """Ни бесплатной квоты трека, ни кредитов: 402 `credits_exhausted` (с офером безлимита)."""

    def __init__(self, available: int) -> None:
        super().__init__("credits_exhausted", f"Доступно {available} генераций", 402)
        self.available = int(available)


async def start_batch(tg_id: int, audio_hash: str, job_id: str, videos: int,
                      credits_left: Callable[[], Awaitable[int]]) -> str:
    """Режим батча сайта — «free» или «credits» — с записью бесплатного батча.

    Бесплатная квота проверяется и списывается одной транзакцией под замком на человека
    (claim_track_batch): два параллельных сабмита больше не уходят бесплатно оба.
    Повтор того же сабмита (тот же ключ идемпотентности) и дозапуск частично
    поставленного батча берут уже записанный режим джобы — квоту заново не считаем,
    бесплатный батч не превращается в батч за кредиты.

    Режим «credits» здесь только выбирается: резерв кредитов и запись батча за кредиты
    делает вызывающий (резерв идемпотентен по джобе). Не хватает ни квоты, ни
    кредитов — TrackLimitError с причиной для окна перезарядки или CreditsExhausted."""
    tg_id, videos = int(tg_id), int(videos)

    def admit(tripwire: bool, unl: dict[str, Any] | None, rows: list[dict[str, Any]]) -> bool:
        q = _quota_of(now=now_utc(), audio_hash=audio_hash, tripwire=tripwire, unl=unl, rows=rows)
        return _free_fits(q, videos)

    # Два круга: между отказом квоты и её чтением для текста ошибки могла кончиться
    # перезарядка — тогда пробуем занять квоту ещё раз, а не отвечаем «лимит».
    for _ in range(2):
        mode = await repo().claim_track_batch(
            tg_id=tg_id, audio_hash=audio_hash, job_id=job_id, videos=videos, admit=admit,
        )
        if mode is not None:
            return mode
        left = int(await credits_left())
        if left >= videos:
            return "credits"
        q = await track_quota(tg_id, audio_hash)
        if q is None:
            raise CreditsExhausted(left)
        if not q.allowed:
            raise TrackLimitError(q.reason, "Лимит на трек пока исчерпан.", q)
        if videos > q.max_videos:
            raise TrackLimitError("track_batch_cap", f"За раз — не больше {q.max_videos} роликов.", q)
    raise RuntimeError(f"track quota flapped twice for job {job_id}")
