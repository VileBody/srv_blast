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

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import quote

from services.tg_bot_public import marketing_texts as mt
from services.tg_bot_public import track_unlimited as tu

from . import telegram_bot
from .runtime import SETTINGS as RUNTIME

log = logging.getLogger(__name__)

ACTION_CHANNEL = "channel_subscribed"
ACTION_MANAGER = "manager_contacted"
UNLOCK_ACTIONS = (ACTION_CHANNEL, ACTION_MANAGER)

MANAGER_USERNAME = os.getenv("WEB_MANAGER_USERNAME", "impulsemanage").strip().lstrip("@")
SUBSCRIPTION_CHANNEL = os.getenv("SUBSCRIPTION_CHANNEL", "@impulsemarketing").strip()
PUBLIC_BOT_USERNAME = os.getenv("WEB_PUBLIC_BOT_USERNAME", "blast808bot").strip().lstrip("@")
# Прямая ссылка на методичку. Пусто — методичку присылает публичный бот документом.
METHODOLOGY_URL = os.getenv("METHODOLOGY_URL", "").strip()


class FunnelError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class _Repo(Protocol):
    async def has_paid(self, tg_id: int) -> bool: ...
    async def get_survey_response(self, tg_id: int) -> dict[str, Any] | None: ...
    async def save_survey_answer(self, tg_id: int, **kw: Any) -> None: ...
    async def save_video_rating(self, tg_id: int, **kw: Any) -> None: ...
    async def list_video_ratings(self, tg_id: int, video_ids: list[str]) -> dict[str, dict[str, Any]]: ...
    async def mark_funnel_action(self, tg_id: int, action: str, detail: str = "") -> bool: ...
    async def funnel_actions(self, tg_id: int) -> dict[str, datetime]: ...
    async def get_track_unlimited(self, tg_id: int) -> dict[str, Any] | None: ...
    async def unlock_track_unlimited(self, tg_id: int, audio_hash: str) -> dict[str, Any]: ...
    async def list_track_batches(self, tg_id: int, audio_hash: str) -> list[dict[str, Any]]: ...
    async def record_track_batch(self, **kw: Any) -> None: ...
    async def release_track_batch(self, job_id: str, videos: int) -> None: ...
    async def drop_track_batch(self, job_id: str) -> None: ...


class MemoryRepo:
    """Тот же контракт, что у credits_db, — в памяти (mock-режим и тесты)."""

    def __init__(self) -> None:
        self.paid: set[int] = set()
        self.surveys: dict[int, dict[str, Any]] = {}
        self.ratings: dict[tuple[int, str], dict[str, Any]] = {}
        self.actions: dict[int, dict[str, datetime]] = {}
        self.unlimited: dict[int, dict[str, Any]] = {}
        self.batches: list[dict[str, Any]] = []

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

    async def list_video_ratings(self, tg_id: int, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {v: self.ratings[(int(tg_id), v)] for v in video_ids if (int(tg_id), v) in self.ratings}

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

    async def release_track_batch(self, job_id: str, videos: int) -> None:
        for b in self.batches:
            if b["job_id"] == job_id:
                b["released"] = min(b["videos"], b["released"] + max(0, int(videos)))

    async def drop_track_batch(self, job_id: str) -> None:
        self.batches = [b for b in self.batches if b["job_id"] != job_id]


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


def check_channel_member(tg_id: int) -> bool:
    """Подписан ли человек на канал — тем же публичным ботом, что проверял онбординг."""
    if RUNTIME.backend != "production":
        return True
    token = os.getenv("WEB_PUBLIC_BOT_TOKEN", "").strip()
    if not token:
        raise FunnelError("channel_check_unavailable", "Проверка подписки не настроена.", 503)
    result = telegram_bot._api("getChatMember", {"chat_id": SUBSCRIPTION_CHANNEL, "user_id": int(tg_id)}, token=token)
    if not result.get("ok"):
        raise FunnelError("channel_check_failed", "Не получилось проверить подписку, попробуй ещё раз.", 503)
    return str((result.get("result") or {}).get("status") or "") in {"member", "administrator", "creator"}


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


async def track_quota(tg_id: int, audio_hash: str) -> tu.TrackQuota | None:
    """Квота трека, если безлимит открыт именно на нём; иначе None."""
    r = repo()
    unl = await r.get_track_unlimited(int(tg_id))
    if not unl or unl["audio_hash"] != audio_hash:
        return None
    rows = await r.list_track_batches(int(tg_id), audio_hash)
    return tu.evaluate(
        now=now_utc(),
        unlocked_at=unl["unlocked_at"],
        tripwire=unl.get("tripwire_paid_at") is not None,
        batches=_batches(rows),
    )


async def state(tg_id: int, *, saved_tracks: list[dict[str, Any]]) -> dict[str, Any]:
    r = repo()
    tg_id = int(tg_id)
    survey = await r.get_survey_response(tg_id) or {}
    actions = await r.funnel_actions(tg_id)
    unl = await r.get_track_unlimited(tg_id)
    unlimited = None
    if unl:
        track = next((t for t in saved_tracks if t.get("audioHash") == unl["audio_hash"]), None)
        q = await track_quota(tg_id, unl["audio_hash"])
        unlimited = {
            # Трек сверяется по хэшу: SavedTrack мог пропасть (или трек открыт из бота),
            # а id на сайте у одного трека бывает не один.
            "audioHash": unl["audio_hash"],
            "trackId": track["id"] if track else None,
            "trackTitle": track_title(track["filename"]) if track else None,
            "unlockedAt": unl["unlocked_at"].isoformat(),
            "tripwire": unl.get("tripwire_paid_at") is not None,
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
        "actions": {a: a in actions for a in UNLOCK_ACTIONS},
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


async def unlock(tg_id: int, audio_hash: str) -> dict[str, Any]:
    """Открыть безлимит: оба действия выполнены. Повторно на другой трек — отказ."""
    r = repo()
    # Воронка конверсионная: платящим безлимит не открываем (у них тариф).
    if await r.has_paid(int(tg_id)):
        raise FunnelError("unlimited_paid", "Безлимит на трек — для бесплатного тарифа.", 409)
    actions = await r.funnel_actions(int(tg_id))
    missing = [a for a in UNLOCK_ACTIONS if a not in actions]
    if missing:
        raise FunnelError("unlock_actions_missing", "Сначала выполни оба шага.", 409)
    row = await r.unlock_track_unlimited(int(tg_id), audio_hash)
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
