from __future__ import annotations

import math
from collections import defaultdict
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from typing import Any
from uuid import uuid4

from .render_job import build_render_job, variation_label

BASE_S3 = "https://s3.twcstorage.ru/f7cef916-asset-storage/app/blast808/media/v1"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# --- Базовое состояние: триал без подключённого TikTok ---
# Правило продукта: пока TikTok не подключён — 5 роликов на аккаунт. Подключил (и прошёл
# верификацию) — ролики в рамках ОДНОГО трека становятся безлимитными. Лимит треков у триала
# всегда 1; платные пакеты расширяют его (Figma W44/W45).
TRIAL_VIDEOS = 5
TRIAL_TRACKS = 1

DEMO_USER_ID = "user_1"


@dataclass
class Workspace:
    """Данные ОДНОГО пользователя.

    Раньше `USER/SUBSCRIPTION/PROJECTS/...` были одним набором на всех: любой новый аккаунт
    открывал демо-проекты «Ночной город»/«Неоновый вайб» и чужую подписку. Теперь у каждого
    юзера свой воркспейс, а модульные имена (`store.USER` и т.п.) — это вид на воркспейс
    текущего запроса (см. `__getattr__` внизу файла).

    Джобы и итерации живут в общих словарях: у них есть `userId`/`projectId`, и выборка идёт
    по владельцу — так же, как это будет одной таблицей в БД.
    """
    user: dict[str, Any]
    subscription: dict[str, Any]
    tiktok: dict[str, Any] | None = None
    projects: list[dict[str, Any]] = field(default_factory=list)
    saved_tracks: list[dict[str, Any]] = field(default_factory=list)
    user_sources: list[dict[str, Any]] = field(default_factory=list)
    active_project_id: str | None = None
    wizard_session: dict[str, Any] | None = None


def _new_subscription(user_id: str) -> dict[str, Any]:
    return {
        "id": f"sub_{user_id}",
        "userId": user_id,
        "tier": "TRIAL",
        # creditsTotal НЕ хранится: он производный от подключения TikTok — см. video_limit()
        "creditsUsed": 0,
        "tracksTotal": TRIAL_TRACKS,
        "tracksUsed": 0,
        "renewsAt": None,
        "isActive": True,
        "startedAt": iso(utcnow()),
        # trial | active | past_due (списание не прошло) | canceled (доступ до конца периода)
        "billingStatus": "trial",
        "cancelAtPeriodEnd": False,
        "lastPaymentError": None,
        # subscription | product | trial — модель оплаты активного плана
        "planKind": "trial",
        "expiresAt": None,
        # сколько бонусов со шкалы месяцев уже забрано
        "bonusesClaimed": 0,
    }


def _empty_workspace(user_id: str, profile: dict[str, Any] | None = None) -> Workspace:
    """Чистый аккаунт: ни проектов, ни треков, ни исходников."""
    profile = profile or {}
    return Workspace(
        user={
            "id": user_id,
            "email": profile.get("email") or "",
            "name": profile.get("name") or "",
            "surname": profile.get("surname") or "",
            "artistNick": profile.get("artistNick"),
            "avatarUrl": None,
            "tgUserId": profile.get("tgUserId"),
            "tgChatId": profile.get("tgChatId"),
            "tgVerified": bool(profile.get("tgVerified")),
            "createdAt": iso(utcnow()),
        },
        subscription=_new_subscription(user_id),
    )


WORKSPACES: dict[str, Workspace] = {}
# ContextVar, а не глобальная переменная: sync-ручки FastAPI выполняются в пуле потоков,
# и общий «текущий юзер» на модуле означал бы гонку между параллельными запросами.
_current_user: ContextVar[str] = ContextVar("blast_current_user", default=DEMO_USER_ID)


def use_user(user_id: str | None) -> None:
    """Переключить контекст запроса на воркспейс юзера (ставится middleware из сессии)."""
    _current_user.set(user_id or DEMO_USER_ID)


def current_user_id() -> str:
    return _current_user.get()


def workspace(user_id: str, profile: dict[str, Any] | None = None) -> Workspace:
    space = WORKSPACES.get(user_id)
    if space is None:
        space = _empty_workspace(user_id, profile)
        WORKSPACES[user_id] = space
    return space


def ws() -> Workspace:
    return workspace(_current_user.get())


def video_limit() -> int | None:
    """Лимит роликов: 5 на триале без TikTok, безлимит (None) — когда TikTok подключён."""
    space = ws()
    if space.subscription["tier"] != "TRIAL":
        return space.subscription.get("creditsTotal")
    return None if space.tiktok else TRIAL_VIDEOS


# Пакеты (Figma W44/W45): видео (None = безлимит), лимит треков и МОДЕЛЬ ОПЛАТЫ.
# Blast — подписка (продлевается, её можно отменить); Glow и Impulse — разовые покупки:
# автопродления у них нет, и показывать им карточку управления подпиской нельзя.
PLAN_SPECS: dict[str, dict[str, Any]] = {
    "BLAST": {"videos": 100, "tracks": 4, "kind": "subscription"},
    "GLOW": {"videos": 400, "tracks": 10, "kind": "product"},
    "IMPULSE": {"videos": None, "tracks": 24, "kind": "product", "days": 365},
}


def plan_kind(tier: str | None) -> str:
    """subscription | product | trial — от этого зависят состояния оплаты в профиле."""
    spec = PLAN_SPECS.get((tier or "").upper())
    return spec["kind"] if spec else "trial"


def activate_plan(package_type: str) -> dict[str, Any]:
    """Активировать оплаченный план.

    Подписка получает дату следующего списания, разовый продукт — нет: у него либо срок
    действия (Impulse — год), либо бессрочный доступ (Glow). Раньше всем ставился renewsAt,
    и купленный продукт выглядел в профиле как подписка.
    """
    sub = ws().subscription
    spec = PLAN_SPECS.get((package_type or "").upper())
    if not spec:
        return sub
    subscription_like = spec["kind"] == "subscription"
    days = spec.get("days")
    sub.update({
        "tier": package_type.upper(),
        "creditsTotal": spec["videos"],  # None → безлимит
        "tracksTotal": spec["tracks"],
        "isActive": True,
        "renewsAt": iso(utcnow() + timedelta(days=30)) if subscription_like else None,
        "expiresAt": iso(utcnow() + timedelta(days=days)) if days else None,
        "planKind": spec["kind"],
        # успешная оплата снимает и past_due, и запланированную отмену
        "billingStatus": "active",
        "cancelAtPeriodEnd": False,
        "lastPaymentError": None,
        # шкала бонусных месяцев считается с момента покупки
        "startedAt": iso(utcnow()),
        "bonusesClaimed": 0,
    })
    return sub


# Награды шкалы бонусных месяцев (Figma Wireframe-44 → 62): два месяца по треку,
# третий — снятие лимита треков.
BONUS_MONTHS = 3


def bonuses_earned(started_at: str | None) -> int:
    """Сколько бонусов уже заработано: по одному за каждый ПОЛНЫЙ месяц подписки."""
    if not started_at:
        return 0
    try:
        start = datetime.fromisoformat(started_at)
    except ValueError:
        return 0
    now = utcnow()
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    months = (now.year - start.year) * 12 + (now.month - start.month)
    if now.day < start.day:
        months -= 1
    return max(0, min(BONUS_MONTHS, months))


def claim_bonus() -> dict[str, Any]:
    """Забрать следующий заработанный бонус: +1 трек, а за третий месяц — снятие лимита.

    Кнопка «Получить» на шкале раньше была декорацией: она меняла подпись по ховеру
    и ничего не делала, хотя это единственный способ получить обещанный бонус.
    """
    sub = ws().subscription
    claimed = int(sub.get("bonusesClaimed") or 0)
    if claimed >= bonuses_earned(sub.get("startedAt")):
        raise ValueError("no_bonus_available")
    if claimed >= BONUS_MONTHS - 1:
        sub["tracksTotal"] = None  # третий месяц — безлимит по трекам
    elif sub.get("tracksTotal") is not None:
        sub["tracksTotal"] = int(sub["tracksTotal"]) + 1
    sub["bonusesClaimed"] = claimed + 1
    return sub


def reset_to_trial() -> dict[str, Any]:
    """Вернуть подписку к триалу (для сброса превью/после отмены)."""
    sub = ws().subscription
    sub.update({
        "tier": "TRIAL",
        "tracksTotal": TRIAL_TRACKS,
        "renewsAt": None,
        "isActive": True,
        "billingStatus": "trial",
        "cancelAtPeriodEnd": False,
        "lastPaymentError": None,
        "planKind": "trial",
        "expiresAt": None,
        "bonusesClaimed": 0,
    })
    sub.pop("creditsTotal", None)
    return sub


def mark_payment_failed(reason: str = "card_declined") -> dict[str, Any]:
    """Списание не прошло: доступ сохраняем, но требуем обновить оплату.

    Подписка не «заканчивается» сама — она либо продлевается, либо повисает в
    `past_due`, пока юзер не обновит платёж или не отменит её сам.
    """
    sub = ws().subscription
    sub.update({"billingStatus": "past_due", "lastPaymentError": reason})
    return sub


def mark_payment_ok() -> dict[str, Any]:
    """Оплата прошла: снимаем past_due и продлеваем период."""
    sub = ws().subscription
    sub.update({
        "billingStatus": "active" if sub["tier"] != "TRIAL" else "trial",
        "lastPaymentError": None,
        "isActive": True,
        "renewsAt": iso(utcnow() + timedelta(days=30)) if sub["tier"] != "TRIAL" else None,
    })
    return sub


def cancel_subscription(at_period_end: bool = True) -> dict[str, Any]:
    """Отмена подписки: по умолчанию доступ живёт до конца оплаченного периода.

    Право отменить в любой момент — обязательное условие оферты, поэтому отмена
    доступна и в `past_due` (иначе юзер заперт в неудачной оплате).
    """
    sub = ws().subscription
    if sub["tier"] == "TRIAL":
        return sub
    if at_period_end and sub.get("renewsAt"):
        sub.update({"cancelAtPeriodEnd": True, "billingStatus": "canceled", "lastPaymentError": None})
        return sub
    return reset_to_trial()


TG_VERIFICATIONS: dict[str, dict[str, Any]] = {}

# Джобы и итерации — общие таблицы: у джоба есть userId, у итерации — projectId,
# выборка всегда идёт по владельцу (см. _owned_jobs).
JOBS: dict[str, dict[str, Any]] = {}
# idempotencyKey визарда -> job id: защита от дабл-клика по «Сгенерировать»
JOB_IDEMPOTENCY: dict[str, str] = {}
ITERATIONS: dict[str, list[dict[str, Any]]] = {}


def _owned_jobs() -> list[dict[str, Any]]:
    """Джобы текущего юзера — чужие в выборку попадать не должны."""
    user_id = ws().user["id"]
    return [job for job in JOBS.values() if job.get("userId") == user_id]


def _seed_demo_workspace() -> Workspace:
    """Демо-аккаунт с наполнением: он и остаётся владельцем сид-данных.

    Все остальные аккаунты создаются пустыми — новый юзер не должен видеть демо-проекты.
    """
    space = _empty_workspace(DEMO_USER_ID, {
        "email": "demo@blast808.com",
        "name": "Макс",
        "surname": "Жаров",
        "artistNick": "808max",
        "tgUserId": "tg_808max",
        "tgVerified": True,
    })
    space.user["createdAt"] = iso(utcnow() - timedelta(days=72))
    space.saved_tracks = [
        {
            "id": "track_prev_1",
            "userId": DEMO_USER_ID,
            "s3Key": f"{BASE_S3}/tracks/user_1/previous/source.mp3",
            "filename": "last-night-demo.mp3",
            "durationS": 204.0,
            "createdAt": iso(utcnow() - timedelta(days=1)),
            "expiresAt": iso(utcnow() + timedelta(days=6)),
        }
    ]
    space.projects = [
        {
            "id": "project_1",
            "userId": DEMO_USER_ID,
            "name": "Ночной город",
            "coverUrl": "/assets/cover-placeholder.svg",
            "packageType": "TRIAL",
            "status": "ACTIVE",
            "startedAt": iso(utcnow() - timedelta(days=18)),
            "endsAt": iso(utcnow() + timedelta(days=12)),
            "views": 0,
            "sparkline": [],
            "generated": 0,
            "total": TRIAL_VIDEOS,
        },
        {
            "id": "project_2",
            "userId": DEMO_USER_ID,
            "name": "Неоновый вайб",
            "coverUrl": "/assets/cover-placeholder.svg",
            "packageType": "TRIAL",
            "status": "IN_PROGRESS",
            "startedAt": iso(utcnow() - timedelta(days=41)),
            "endsAt": iso(utcnow() - timedelta(days=11)),
            "views": 0,
            "sparkline": [],
            "generated": 0,
            "total": TRIAL_VIDEOS,
        },
    ]
    space.active_project_id = "project_1"
    # сид уже содержит один трек — счётчик лимита должен это отражать
    space.subscription["tracksUsed"] = len(space.saved_tracks)
    return space


WORKSPACES[DEMO_USER_ID] = _seed_demo_workspace()

# Фикстуры трёх планов подбора — тех же, что у бота (vibes 9:16 / cine16x9 / films).
# Планы нужны и в моке: без них степпер типов футажей нечем проверить, а именно на
# нём и вылезло, что переключение ничего не меняло.
VIBES: list[dict[str, Any]] = [
    {"id": "night-city", "name": "Ночной город", "plane": "vibes", "score": 0.93, "previewUrl": f"{BASE_S3}/vibes/night-city/preview.mp4"},
    {"id": "neon", "name": "Неон", "plane": "vibes", "score": 0.89, "previewUrl": f"{BASE_S3}/vibes/neon/preview.mp4"},
    {"id": "sunset", "name": "Закат", "plane": "vibes", "score": 0.76, "previewUrl": f"{BASE_S3}/vibes/sunset/preview.mp4"},
    {"id": "backstage", "name": "Бэкстейдж", "plane": "vibes", "score": 0.71, "previewUrl": f"{BASE_S3}/vibes/backstage/preview.mp4"},
    {"id": "street", "name": "Улица", "plane": "vibes", "score": 0.68, "previewUrl": f"{BASE_S3}/vibes/street/preview.mp4"},
    {"id": "cine-new-york", "name": "Нью-Йорк", "plane": "cine16x9", "score": 0.88, "previewUrl": f"{BASE_S3}/vibes/cine-new-york/preview.mp4"},
    {"id": "cine-tokyo", "name": "Киото", "plane": "cine16x9", "score": 0.74, "previewUrl": f"{BASE_S3}/vibes/cine-tokyo/preview.mp4"},
    {"id": "film-brat", "name": "Брат", "plane": "films", "score": 0.9, "previewUrl": f"{BASE_S3}/vibes/film-brat/preview.mp4"},
    {"id": "film-bumer", "name": "Бумер", "plane": "films", "score": 0.72, "previewUrl": f"{BASE_S3}/vibes/film-bumer/preview.mp4"},
]

PHOTOS: list[dict[str, Any]] = [
    {"id": "photo-desert", "name": "Пустынный закат", "score": 0.9, "previewUrl": f"{BASE_S3}/photos/desert/preview.jpg"},
    {"id": "photo-portrait", "name": "Крупный план", "score": 0.84, "previewUrl": f"{BASE_S3}/photos/portrait/preview.jpg"},
    {"id": "photo-studio", "name": "Студийный свет", "score": 0.77, "previewUrl": f"{BASE_S3}/photos/studio/preview.jpg"},
    {"id": "photo-street", "name": "Уличный кадр", "score": 0.65, "previewUrl": f"{BASE_S3}/photos/street/preview.jpg"},
]

SUBTITLE_STYLES: list[dict[str, Any]] = [
    {"id": "brat", "name": "Brat", "previewUrl": f"{BASE_S3}/subtitles/brat/preview.jpg"},
    {"id": "jakson", "name": "Jakson", "previewUrl": f"{BASE_S3}/subtitles/jakson/preview.jpg"},
    {"id": "impulse", "name": "Impulse", "previewUrl": f"{BASE_S3}/subtitles/impulse/preview.jpg"},
    {"id": "tape", "name": "Tape", "previewUrl": f"{BASE_S3}/subtitles/tape/preview.jpg"},
    {"id": "trendy", "name": "Trendy", "previewUrl": f"{BASE_S3}/subtitles/trendy/preview.jpg"},
]

DROPS: list[dict[str, Any]] = [
    {"time": "01:34", "seconds": 94, "best": True, "confidence": 0.94},
    {"time": "01:36", "seconds": 96, "best": False, "confidence": 0.82},
    {"time": "01:41", "seconds": 101, "best": False, "confidence": 0.78},
]


def connect_tiktok(handle: str, open_id: str, tokens: dict[str, Any], avatar_url: str | None = None) -> dict[str, Any]:
    """Expose only public account metadata; OAuth secrets live in tiktok_token_store."""
    space = ws()
    expires_in = int(tokens.get("expires_in") or 0)
    space.tiktok = {
        "userId": space.user["id"],
        "handle": handle.lstrip("@"),
        "openId": open_id,
        "avatarUrl": avatar_url,
        "scopes": tokens.get("scope"),
        "expiresAt": iso(utcnow() + timedelta(seconds=expires_in)) if expires_in else None,
    }
    return deepcopy(space.tiktok)


def disconnect_tiktok() -> None:
    ws().tiktok = None


def find_video(video_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return the mutable job/video pair for a generated version id."""
    for job in _owned_jobs():
        video = next((item for item in job.get("videos", []) if item.get("id") == video_id), None)
        if video:
            return job, video
    return None


# --- Аналитика итераций: ведущий ПАРАМЕТР, а не победившая связка ------------
#
# Раньше сравнивались связки целиком («Ночной город | Brat | Без хука» против другой связки).
# Такой ответ невозможно применить: человек не знает, что именно сработало. Теперь каждое
# измерение разбирается отдельно — все ролики с Brat против всех остальных.
#
# Согласовано с владельцем: минимум 2 ролика на значение, порог отрыва 25%.
MIN_VIDEOS_PER_VALUE = 2
LIFT_THRESHOLD = 0.25
# Пол для оценки разброса: без него при одинаковых внутри группы результатах деление на ноль
# давало бы «бесконечную уверенность» на двух роликах.
MIN_RELATIVE_SPREAD = 0.05

# Измерение → поле ролика. Ровно то, что человек выбирает в визарде.
DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("background", "source"),
    ("subtitles", "subtitleStyle"),
    ("fx", "hook"),
)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    """Разброс по совокупности (не по выборке): выборки тут по 2–3 ролика,
    и деление на n-1 раздувало бы оценку до бессмысленной."""
    if len(values) < 2:
        return 0.0
    average = _mean(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / len(values))


def _analysis_videos(project_id: str) -> list[dict[str, Any]]:
    """Ролики проекта, по которым есть что мерить — то есть опубликованные.

    Неопубликованный ролик просмотров не имеет по определению, и держать его в сравнении
    значит сравнивать с нулём. Мок без ключей TikTok подставляет стабильное псевдо-значение,
    чтобы флоу гонялся локально; на настоящих метриках эта ветка не включается.
    """
    videos: list[dict[str, Any]] = []
    for job in _owned_jobs():
        if job.get("projectId") != project_id:
            continue
        for video in job.get("videos", []):
            if not (video.get("postedAt") or video.get("tiktokPublishId")):
                continue
            values = {name: str(video.get(field) or "") for name, field in DIMENSIONS}
            metrics = video.get("metrics") or {}
            views = int(metrics.get("view_count") or metrics.get("views") or 0)
            engagement = (int(metrics.get("like_count") or 0) + int(metrics.get("comment_count") or 0)
                          + int(metrics.get("share_count") or 0))
            if views <= 0:
                label = " | ".join(values.values())
                views = 280 + (sum(label.encode("utf-8")) + int(video.get("index") or 0) * 173) % 920
            videos.append({
                "id": video.get("id"),
                "jobId": job.get("id"),
                "values": values,
                "views": views,
                "engagement": engagement,
                # лайк/коммент/шер весят больше просмотра: они и двигают выдачу
                "score": views + engagement * 8,
            })
    return videos


def _entangled(videos: list[dict[str, Any]]) -> dict[str, str]:
    """Измерения, вклад которых невозможно разделить никакой математикой.

    Если «Ночной город» всегда шёл вместе с Brat, то ролики с Ночным городом — это ровно
    те же ролики, что с Brat, и любой отрыв принадлежит паре, а не одному из них. Это
    свойство РАСКЛАДКИ (что человек набрал на этапе «Пул»), а не данных, поэтому и лечится
    оно только следующим батчем: развести значения.
    """
    blocked: dict[str, str] = {}
    counts = {name: defaultdict(int) for name, _ in DIMENSIONS}
    for video in videos:
        for name, _ in DIMENSIONS:
            counts[name][video["values"][name]] += 1
    for first, second in combinations([name for name, _ in DIMENSIONS], 2):
        # Считаем только по значениям, которые вообще читаются. Иначе два ролика с разными
        # настройками выглядят «слипшимися» просто потому, что каждое значение встретилось
        # ровно раз — а это не слипание, а нехватка данных (отдельный вердикт).
        ready = [video for video in videos
                 if counts[first][video["values"][first]] >= MIN_VIDEOS_PER_VALUE
                 and counts[second][video["values"][second]] >= MIN_VIDEOS_PER_VALUE]
        forward: dict[str, set[str]] = defaultdict(set)
        backward: dict[str, set[str]] = defaultdict(set)
        for video in ready:
            forward[video["values"][first]].add(video["values"][second])
            backward[video["values"][second]].add(video["values"][first])
        if len(forward) < 2 or len(backward) < 2:
            continue  # одно значение — это отдельный вердикт, не слипание
        if all(len(item) == 1 for item in forward.values()) and all(len(item) == 1 for item in backward.values()):
            blocked.setdefault(first, second)
            blocked.setdefault(second, first)
    return blocked


def _analyze_dimension(name: str, videos: list[dict[str, Any]], entangled_with: str | None) -> dict[str, Any]:
    """Один из четырёх вердиктов по измерению.

    signal        — есть лидер и отрыв, который выдержал поправку на число значений;
    no_difference — данных хватило, но значения равны (тоже ответ: параметр не решает);
    low_data      — не хватает роликов, чтобы хоть два значения читались;
    blocked       — проверить нельзя: одно значение либо слиплось с другим измерением.
    """
    groups: dict[str, list[float]] = defaultdict(list)
    views: dict[str, int] = defaultdict(int)
    for video in videos:
        value = video["values"][name]
        groups[value].append(float(video["score"]))
        views[value] += video["views"]

    payload = sorted(
        ({"value": value, "videos": len(scores), "averageScore": round(_mean(scores), 2), "views": views[value]}
         for value, scores in groups.items()),
        key=lambda item: item["averageScore"], reverse=True,
    )
    ready = {value: scores for value, scores in groups.items() if len(scores) >= MIN_VIDEOS_PER_VALUE}
    result: dict[str, Any] = {
        "dimension": name,
        "values": payload,
        "leader": None,
        "liftPercent": 0.0,
        "confidence": 0.0,
        "videosNeeded": 0,
        "blockedBy": None,
    }

    if entangled_with:
        result.update({"verdict": "blocked", "blockedBy": f"entangled:{entangled_with}"})
    elif len(groups) < 2:
        result.update({"verdict": "blocked", "blockedBy": "single_value"})
    elif len(ready) < 2:
        # сколько роликов не хватает: добить до двух значений по MIN_VIDEOS_PER_VALUE
        biggest = sorted((len(scores) for scores in groups.values()), reverse=True)[:2]
        result.update({
            "verdict": "low_data",
            "videosNeeded": sum(max(0, MIN_VIDEOS_PER_VALUE - size) for size in biggest),
        })
    else:
        leader = max(ready, key=lambda value: _mean(ready[value]))
        leader_scores = ready[leader]
        rest_scores = [score for value, scores in ready.items() if value != leader for score in scores]
        leader_mean, rest_mean = _mean(leader_scores), _mean(rest_scores)
        lift = (leader_mean / rest_mean - 1) if rest_mean else 0.0
        pooled = leader_scores + rest_scores
        spread = max(_stdev(pooled) / _mean(pooled) if _mean(pooled) else 0.0, MIN_RELATIVE_SPREAD)
        # нормировка на размер выборки: тот же отрыв на 2+2 роликах стоит меньше, чем на 5+5
        error = spread * math.sqrt(1 / len(leader_scores) + 1 / len(rest_scores))
        # поправка на число значений: чем их больше, тем выше «случайный максимум» из них
        penalty = math.sqrt(2 * math.log(len(ready)))
        confidence = lift / error - penalty
        leader_payload = next(item for item in payload if item["value"] == leader)
        result.update({
            "verdict": "signal" if lift >= LIFT_THRESHOLD and confidence > 0 else "no_difference",
            "leader": deepcopy(leader_payload),
            "liftPercent": round(lift * 100, 1),
            "confidence": round(confidence, 2),
        })
    return result


def _combination_winner(videos: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Лучшая связка целиком. Пользователю её больше не показываем, но следующая итерация
    должна от чего-то отталкиваться — ей нужен родительский батч."""
    if not videos:
        return None
    slices: dict[str, dict[str, Any]] = {}
    for video in videos:
        key = " | ".join(video["values"].values())
        item = slices.setdefault(key, {
            "key": key, "views": 0, "score": 0.0, "videos": 0, "jobId": video["jobId"],
            **video["values"],
        })
        item["views"] += video["views"]
        item["score"] += video["score"]
        item["videos"] += 1
    for item in slices.values():
        item["averageScore"] = round(item["score"] / max(1, item["videos"]), 2)
    return deepcopy(max(slices.values(), key=lambda item: (item["averageScore"], item["views"])))


def analyze_iterations(project_id: str) -> dict[str, Any]:
    """Разбор роликов проекта по каждому измерению отдельно.

    Созревают измерения неодновременно: где-то человек варьировал футаж при одном стиле
    субтитров, где-то наоборот. Поэтому общего порога «10 роликов» больше нет — отдаём по
    каждому измерению его собственный вердикт, а интерфейс показывает то, что уже читается.
    """
    videos = _analysis_videos(project_id)
    entangled = _entangled(videos)
    dimensions = [_analyze_dimension(name, videos, entangled.get(name)) for name, _ in DIMENSIONS]
    signals = [item for item in dimensions if item["verdict"] == "signal"]
    leading = max(signals, key=lambda item: item["confidence"]) if signals else None
    pending = [item["videosNeeded"] for item in dimensions if item["verdict"] == "low_data"]
    readable = [item for item in dimensions if item["verdict"] in ("signal", "no_difference")]
    return {
        "projectId": project_id,
        "videosAnalyzed": len(videos),
        "dimensions": deepcopy(dimensions),
        # какое измерение «прострелило»: из тех, где сигнал есть, самое уверенное
        "leadingDimension": leading["dimension"] if leading else None,
        # сколько роликов до ближайшего читаемого измерения (0 — уже есть что показать)
        "videosNeeded": 0 if readable else (min(pending) if pending else MIN_VIDEOS_PER_VALUE * 2),
        "enoughData": bool(readable),
        "minVideosPerValue": MIN_VIDEOS_PER_VALUE,
        "liftThresholdPercent": round(LIFT_THRESHOLD * 100),
        # для следующей итерации нужен родительский батч — берём лучшую связку
        "winner": _combination_winner(videos),
    }


def create_iteration(project_id: str, videos_to_generate: int, test_parameter: str = "subtitles") -> tuple[dict[str, Any], dict[str, Any]]:
    project_jobs = [job for job in _owned_jobs() if job.get("projectId") == project_id]
    if not project_jobs:
        raise ValueError("A completed batch is required before creating an iteration")
    report = analyze_iterations(project_id)
    winner_job_id = (report.get("winner") or {}).get("jobId")
    source_job = JOBS.get(winner_job_id) if winner_job_id else None
    if source_job is None:
        source_job = max(project_jobs, key=lambda item: item.get("createdAt") or "")
    number = len(ITERATIONS.get(project_id, [])) + 2
    stage_data = deepcopy(source_job.get("stageData") or {})
    stage_data["iteration"] = {
        "number": number,
        "parentJobId": source_job.get("id"),
        "winner": deepcopy(report.get("winner")),
        "fixed": ["background"],
        "test": [test_parameter],
    }
    job = create_job(project_id, stage_data, videos_to_generate)
    record = {
        "id": f"iteration_{uuid4().hex[:8]}",
        "projectId": project_id,
        "number": number,
        "parentJobId": source_job.get("id"),
        "jobId": job["id"],
        "testParameter": test_parameter,
        "winner": deepcopy(report.get("winner")),
        "createdAt": iso(utcnow()),
    }
    ITERATIONS.setdefault(project_id, []).append(record)
    return deepcopy(record), job


def list_iterations(project_id: str) -> list[dict[str, Any]]:
    return deepcopy(ITERATIONS.get(project_id, []))


def get_user_bundle() -> dict[str, Any]:
    # creditsTotal — производная от подключения TikTok (video_limit), а не хранимое поле:
    # иначе состояние «подключил TikTok → безлимит» пришлось бы синхронизировать руками.
    total = video_limit()
    space = ws()
    sub = deepcopy(space.subscription)
    sub["creditsTotal"] = total
    user = deepcopy(space.user)
    # Вход через Telegram не спрашивает ФИО — по флагу фронт требует дозаполнить профиль
    user["profileComplete"] = bool((user.get("name") or "").strip())
    return {
        "user": user,
        "subscription": sub,
        "tiktok": deepcopy(space.tiktok),
        # None — безлимит по видео (Figma W43): остаток не считаем
        "creditsLeft": None if total is None else max(0, total - space.subscription["creditsUsed"]),
    }


def _project_generated(project_id: str) -> int:
    """Сколько роликов реально сгенерировано для проекта — считаем по JOBS (источник правды),
    а не по статичному полю, иначе «Текущие видео» залипает на 0."""
    return sum(len(job.get("videos", [])) for job in _owned_jobs() if job.get("projectId") == project_id)


def _project_posted(project_id: str) -> int:
    """Сколько роликов проекта реально ушло в TikTok.

    Считаем по отметке на самом ролике (`postedAt` ставится при успешной публикации), а не
    по статусу проекта: статус — это стадия жизненного цикла, он ничего не знает о том,
    сколько штук из батча человек довыложил.
    """
    return sum(
        1
        for job in _owned_jobs() if job.get("projectId") == project_id
        for video in job.get("videos", [])
        if video.get("postedAt") or video.get("tiktokPublishId")
    )


def _last_generation_at(project_id: str) -> str:
    """Когда по проекту в последний раз запускали генерацию («» — ни разу)."""
    return max((str(job.get("createdAt") or "") for job in _owned_jobs()
                if job.get("projectId") == project_id), default="")


def _display_status(project: dict[str, Any]) -> str:
    """Стадия проекта считается, а не хранится.

    Правило владельца: проект «Завершён» только когда человек ушёл работать в другой —
    то есть по ДРУГОМУ проекту генерация запускалась позже. Раньше статус ставил
    рендер-воркер по факту готовности батча, и единственный проект в аккаунте становился
    «Завершён» сразу после первой генерации. Считаем на чтении, чтобы правило чинило и
    уже сохранённые статусы, а не только новые.
    """
    mine = _last_generation_at(project["id"])
    if not mine:
        return "ACTIVE"  # создан, генераций ещё не было
    newer = any(_last_generation_at(other["id"]) > mine
                for other in ws().projects if other["id"] != project["id"])
    return "COMPLETED" if newer else "IN_PROGRESS"


def _enrich_project(project: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(project)
    data["status"] = _display_status(project)
    data["generated"] = _project_generated(project["id"])
    # «N из M выложено»: артисту важнее не общее число роликов, а сколько из них уже в TikTok
    data["posted"] = _project_posted(project["id"])
    # total = потолок роликов: None → безлимит (TikTok подключён) → на фронте «∞» в «Сделать ещё»
    data["total"] = video_limit()
    data["isCurrent"] = project["id"] == current_project_id()
    data["archived"] = bool(project.get("archived"))
    return data


def current_project_id() -> str | None:
    """Текущий проект — явное состояние, а не «первый со статусом ACTIVE».

    Статус — это жизненный цикл проекта (ACTIVE → IN_PROGRESS → COMPLETED), и по нему
    нельзя определить, с каким проектом юзер работает сейчас: после генерации статус
    уходит с ACTIVE, а «текущим» проект быть не перестаёт.
    """
    space = ws()
    if space.active_project_id and any(p["id"] == space.active_project_id for p in space.projects):
        return space.active_project_id
    # архивные в «текущие» не назначаем — их специально убрали с глаз
    fallback = next((p for p in space.projects if not p.get("archived")), None)
    space.active_project_id = fallback["id"] if fallback else None
    return space.active_project_id


def set_current_project(project_id: str) -> dict[str, Any] | None:
    space = ws()
    project = next((p for p in space.projects if p["id"] == project_id), None)
    if not project:
        return None
    space.active_project_id = project_id
    return _enrich_project(project)


def list_projects() -> dict[str, Any]:
    projects = sorted(ws().projects, key=lambda p: p["startedAt"], reverse=True)
    enriched = [_enrich_project(p) for p in projects]
    current = current_project_id()
    active = next((p for p in enriched if p["id"] == current), None)
    return {"projects": enriched, "activeProject": deepcopy(active), "mock": True}


def get_project(project_id: str) -> dict[str, Any] | None:
    project = next((p for p in ws().projects if p["id"] == project_id), None)
    if not project:
        return None
    jobs = [j for j in _owned_jobs() if j["projectId"] == project_id]
    data = _enrich_project(project)
    data["jobs"] = deepcopy(jobs)
    return data


def create_project(name: str, package_type: str = "TRIAL", cover_choice: str = "auto") -> dict[str, Any]:
    space = ws()
    pid = f"project_{uuid4().hex[:8]}"
    project = {
        "id": pid,
        "userId": space.user["id"],
        "name": name or "Новый проект",
        "coverUrl": "/assets/cover-placeholder.svg",
        "packageType": package_type,
        # Новый проект пуст: ACTIVE здесь — стадия «создан, генераций ещё не было»,
        # а не «текущий» (текущий живёт в Workspace.active_project_id)
        "status": "ACTIVE",
        "startedAt": iso(utcnow()),
        "endsAt": iso(utcnow() + timedelta(days=30)),
        "views": 0,
        "sparkline": [0, 0, 0, 0, 0, 0, 0],
        "generated": 0,
        "total": video_limit() or TRIAL_VIDEOS,
        "coverChoice": cover_choice,
    }
    space.projects.insert(0, project)
    # Юзера сразу редиректит в новый проект — он же и становится текущим
    space.active_project_id = pid
    return deepcopy(project)


def rename_project(project_id: str, name: str) -> dict[str, Any] | None:
    project = next((p for p in ws().projects if p["id"] == project_id), None)
    if not project:
        return None
    project["name"] = name
    return _enrich_project(project)


def set_project_archived(project_id: str, archived: bool) -> dict[str, Any] | None:
    """Архив — мягкое скрытие: лента не зарастает, но ролики и статистика остаются на месте."""
    space = ws()
    project = next((p for p in space.projects if p["id"] == project_id), None)
    if not project:
        return None
    project["archived"] = archived
    if archived and space.active_project_id == project_id:
        space.active_project_id = next((p["id"] for p in space.projects if not p.get("archived")), None)
    return _enrich_project(project)


def delete_project(project_id: str) -> bool:
    """Удаляет проект вместе с его джобами и роликами. Лимит треков не возвращаем:
    он считается по загруженным трекам (saved_tracks), а не по проектам."""
    space = ws()
    project = next((p for p in space.projects if p["id"] == project_id), None)
    if not project:
        return False
    space.projects.remove(project)
    for job_id in [jid for jid, job in JOBS.items() if job.get("projectId") == project_id and job.get("userId") == space.user["id"]]:
        JOBS.pop(job_id, None)
    for key in [k for k, v in JOB_IDEMPOTENCY.items() if v not in JOBS]:
        JOB_IDEMPOTENCY.pop(key, None)
    if space.active_project_id == project_id:
        space.active_project_id = next((p["id"] for p in space.projects if not p.get("archived")), None)
    return True


def _new_video(job_id: str, index: int, source: str, style: str, hook: str) -> dict[str, Any]:
    return {
        "id": f"{job_id}_v{index}",
        "index": index,
        "status": "PENDING",
        "progress": 0,
        "source": source,
        "subtitleStyle": style,
        "hook": hook,
        "thumbnailUrl": "/assets/cover-placeholder.svg",
        "downloadUrl": None,
    }


def create_job(
    project_id: str,
    stage_data: dict[str, Any],
    videos_to_generate: int,
    idempotency_key: str | None = None,
    *,
    enqueue_mock: bool = True,
) -> dict[str, Any]:
    space = ws()
    # Повтор того же сабмита (дабл-клик, ретрай сети) не должен плодить джобы и жечь кредиты.
    if idempotency_key:
        known = JOB_IDEMPOTENCY.get(idempotency_key)
        if known and known in JOBS:
            return deepcopy(JOBS[known])
    jid = f"job_{uuid4().hex[:8]}"
    # Собираем настоящий render_job из стора v4 (spec: backend/docs/RENDER_JOB_SPEC.md).
    render_job = build_render_job(jid, project_id, space.user["id"], stage_data, videos_to_generate)
    videos = []
    for var in render_job["variations"]:
        lbl = variation_label(var)
        videos.append(_new_video(jid, var["index"], lbl["source"], lbl["subtitleStyle"], lbl["hook"]))
    job = {
        "id": jid,
        "projectId": project_id,
        "userId": space.user["id"],
        "orchestratorJobId": f"mock_orch_{jid}" if enqueue_mock else None,
        "stageData": stage_data,
        "renderJob": render_job,
        "status": "PROCESSING",
        "versions": len(videos),
        "rating": None,
        "outputUrls": [],
        "createdAt": iso(utcnow()),
        "completedAt": None,
        "videos": videos,
        "mock": enqueue_mock,
    }
    JOBS[jid] = job
    if idempotency_key:
        JOB_IDEMPOTENCY[idempotency_key] = jid
    # При безлимите (TikTok подключён) потолка нет — просто копим расход
    used = space.subscription["creditsUsed"] + len(videos)
    total = video_limit()
    space.subscription["creditsUsed"] = used if total is None else min(total, used)
    project = next((p for p in space.projects if p["id"] == project_id), None)
    if project:
        # Хранимое поле — только отметка «генерации по проекту были». Что показывать
        # («В процессе» или «Завершён») решает _display_status по времени последней
        # генерации у соседей: к проекту всегда можно вернуться и добавить батч.
        project["status"] = "IN_PROGRESS"
    if enqueue_mock:
        # Development-only timer worker. Production is enqueued explicitly via
        # production_backend and never reaches this branch.
        from . import render_store, render_worker
        render_worker.ensure_started()
        render_store.get_store().enqueue(job)
    return deepcopy(job)


def rollback_job_creation(job_id: str) -> bool:
    """Remove a job that could not be enqueued and restore its local quota.

    This is valid only before any external orchestrator job was created.  A
    partially enqueued batch must stay persisted so an idempotent retry can
    continue from the first missing variation.
    """
    space = ws()
    job = JOBS.get(job_id)
    if not job or job.get("userId") != space.user["id"]:
        return False
    if any(video.get("orchestratorJobId") for video in job.get("videos", [])):
        raise ValueError("cannot roll back a partially enqueued job")
    JOBS.pop(job_id, None)
    for key, value in list(JOB_IDEMPOTENCY.items()):
        if value == job_id:
            JOB_IDEMPOTENCY.pop(key, None)
    spent = len(job.get("videos") or [])
    space.subscription["creditsUsed"] = max(
        0,
        int(space.subscription.get("creditsUsed") or 0) - spent,
    )
    return True


def find_video(video_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Найти ролик по id (`{job_id}_v{index}`) вместе с его джобом."""
    for job in JOBS.values():
        for video in job.get("videos", []):
            if video["id"] == video_id:
                return job, video
    return None


# Сколько кадров показывает пикер обложки (совпадает с COVER_FRAME_COUNT на фронте)
COVER_FRAMES = 8


def video_frames(video_id: str, count: int = COVER_FRAMES) -> dict[str, Any] | None:
    """Раскадровка ролика под пикер обложки в модалке выкладки.

    Фронт раньше сам перематывал по восемь <video> на один и тот же файл: это тянуло видео
    целиком, зависело от range-запросов и на битой ссылке давало пустые кадры. Здесь отдаём
    готовые кадры с таймкодами — модалка складывает их в стейт и рисует превью.

    В моке кадры указывают на постер ролика; на реальной ноде на эти пути кладёт нарезку
    ffmpeg (`-vf fps=...`) рядом с готовым mp4 — контракт ответа при этом не меняется.
    """
    found = find_video(video_id)
    if not found:
        return None
    job, video = found
    duration_ms = int((job.get("renderJob") or {}).get("durationMs") or 0) or 14_014
    ready = video["status"] == "COMPLETED"
    frames = []
    for idx in range(count):
        # первый кадр — 0, последний — конец ролика минус небольшой запас
        ts = int(duration_ms * idx / max(1, count - 1))
        frames.append({
            "index": idx,
            "timestampMs": min(ts, max(0, duration_ms - 50)),
            "url": video.get("thumbnailUrl") if ready else None,
        })
    return {
        "videoId": video_id,
        "jobId": job["id"],
        "status": video["status"],
        "durationMs": duration_ms,
        "count": count,
        "frames": frames,
    }


def get_job(job_id: str) -> dict[str, Any] | None:
    # статус живёт в job-dict (его двигает render_worker через store); seeded-джобы статичны
    job = JOBS.get(job_id)
    # чужой джоб по прямой ссылке отдавать нельзя — для клиента его просто нет
    if not job or job.get("userId") != ws().user["id"]:
        return None
    return deepcopy(job)


def active_job() -> dict[str, Any] | None:
    for job in _owned_jobs():
        if job["status"] in {"PENDING", "PROCESSING"}:
            return deepcopy(job)
    return None


def tracks_left() -> int | None:
    """Сколько ещё треков можно взять. None — безлимит (tracksTotal не задан)."""
    space = ws()
    total = space.subscription.get("tracksTotal")
    if total is None:
        return None
    return max(0, total - space.subscription.get("tracksUsed", 0))


def save_track(
    filename: str,
    file_path: Path | None = None,
    duration_s: float = 204.0,
    *,
    s3_url: str | None = None,
    playback_url: str | None = None,
    audio_hash: str | None = None,
) -> dict[str, Any]:
    space = ws()
    item = {
        "id": f"track_{uuid4().hex[:8]}",
        "userId": space.user["id"],
        "s3Key": s3_url or f"{BASE_S3}/tracks/{space.user['id']}/{uuid4().hex[:8]}/source.mp3",
        "filename": filename,
        "durationS": duration_s,
        "localUrl": playback_url or (f"/static/uploads/tracks/{file_path.name}" if file_path else None),
        "createdAt": iso(utcnow()),
        "expiresAt": iso(utcnow() + timedelta(days=30)),
        "audioHash": audio_hash,
    }
    space.saved_tracks.insert(0, item)
    # Лимит считается по загруженным трекам: повторный выбор уже сохранённого трека
    # («предыдущий трек» в визарде) счётчик не двигает.
    space.subscription["tracksUsed"] = space.subscription.get("tracksUsed", 0) + 1
    return deepcopy(item)


def save_source(
    filename: str,
    file_path: Path | None = None,
    *,
    s3_url: str | None = None,
    playback_url: str | None = None,
) -> dict[str, Any]:
    """Свой исходник (Figma W39/W49): имя = ключ в background.uploads → render_job."""
    space = ws()
    item = {
        "id": f"src_{uuid4().hex[:8]}",
        "userId": space.user["id"],
        "s3Key": s3_url or f"{BASE_S3}/sources/{space.user['id']}/{uuid4().hex[:8]}/{file_path.name if file_path else 'source.mp4'}",
        "name": filename,
        "localUrl": playback_url or (f"/static/uploads/sources/{file_path.name}" if file_path else None),
        "createdAt": iso(utcnow()),
    }
    space.user_sources.insert(0, item)
    return deepcopy(item)


def previous_track() -> dict[str, Any] | None:
    tracks = ws().saved_tracks
    return deepcopy(tracks[0]) if tracks else None


def set_wizard_session(payload: dict[str, Any]) -> dict[str, Any]:
    space = ws()
    space.wizard_session = {
        "id": "session_1",
        "userId": space.user["id"],
        "projectId": payload.get("projectId"),
        "stage": payload.get("stage", 1),
        "data": payload.get("data", {}),
        "updatedAt": iso(utcnow()),
    }
    return deepcopy(space.wizard_session)


def get_wizard_session() -> dict[str, Any] | None:
    return deepcopy(ws().wizard_session)


def register_user(payload: dict[str, Any]) -> dict[str, Any]:
    user_id = f"user_{uuid4().hex[:8]}"
    verify = {
        "id": f"verify_{uuid4().hex[:8]}",
        "userId": user_id,
        "token": f"verify_{uuid4().hex[:10]}",
        "verified": False,
        "polls": 0,
        "createdAt": iso(utcnow()),
    }
    TG_VERIFICATIONS[user_id] = verify
    return {
        "user": {
            "id": user_id,
            "email": payload.get("email"),
            "name": payload.get("name"),
            "surname": payload.get("surname"),
            "artistNick": None,
            "tgVerified": False,
        },
        "verification": verify,
        "deepLink": f"https://t.me/blast808bot?start={verify['token']}",
        "mock": True,
    }


# Модульные имена USER/SUBSCRIPTION/... оставлены как вид на воркспейс ТЕКУЩЕГО запроса:
# так все существующие обращения `store.USER[...]`, `store.PROJECTS.append(...)` продолжают
# работать и при этом больше не делят данные между аккаунтами. Возвращаются живые объекты
# воркспейса, поэтому мутации на месте по-прежнему применяются.
_WORKSPACE_VIEWS = {
    "USER": "user",
    "SUBSCRIPTION": "subscription",
    "TIKTOK": "tiktok",
    "PROJECTS": "projects",
    "SAVED_TRACKS": "saved_tracks",
    "USER_SOURCES": "user_sources",
    "WIZARD_SESSION": "wizard_session",
    "ACTIVE_PROJECT_ID": "active_project_id",
}


def __getattr__(name: str) -> Any:
    attr = _WORKSPACE_VIEWS.get(name)
    if attr is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(ws(), attr)
