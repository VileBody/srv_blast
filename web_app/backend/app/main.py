from __future__ import annotations

import hashlib
import logging
import os
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from . import mock_store as store
from . import analytics, auth_store, fraud_guard, google_auth, persistence, security, telegram_bot
from . import render_job as render_job_builder
from . import tiktok_api, tiktok_config, tiktok_token_store
from .runtime import SETTINGS as RUNTIME


logger = logging.getLogger(__name__)


def _production_backend():
    from .production_backend import get_backend

    return get_backend()


def _production_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "production_backend_unavailable", "message": str(exc)},
    )


def _billing_backend():
    from .billing_backend import get_billing

    return get_billing()


def _telegram_chat_id() -> int:
    value = auth_store.chat_id_for_user(store.current_user_id()) or store.ws().user.get("tgChatId")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "telegram_required",
                "message": "Привяжи Telegram, чтобы использовать общий баланс и оплату.",
            },
        ) from exc


async def _sync_billing_bundle(data: dict[str, Any]) -> dict[str, Any]:
    snapshot = await _billing_backend().snapshot(_telegram_chat_id())
    subscription = data["subscription"]
    subscription.update({key: value for key, value in snapshot.items() if key not in {"creditsLeft", "tracksLeft"}})
    data["creditsLeft"] = snapshot["creditsLeft"]
    return data

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
UPLOAD_DIR = STATIC_DIR / "uploads" / "tracks"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SOURCE_DIR = STATIC_DIR / "uploads" / "sources"
SOURCE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Blast Web App",
    version="1.0.0",
    description="Blast web application API.",
)
# Список origin-ов — из окружения: в проде фронт живёт на своём домене, и хардкод
# localhost:5173 означал бы либо неработающий прод, либо правку кода на каждый домен.
# Пустой BLAST_CORS_ORIGINS = дев-значение по умолчанию.
CORS_ORIGINS = list(RUNTIME.cors_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    # Заголовки перечислены явно: с "*" браузер не пустил бы X-CSRF-Token при credentials
    allow_headers=["Content-Type", "Authorization", security.CSRF_HEADER],
)
# The SPA is served by nginx. FastAPI only exposes development uploads under
# this narrow mount; the retired Jinja site and its assets are not routable.
app.mount("/static/uploads", StaticFiles(directory=STATIC_DIR / "uploads"), name="uploads")


# Гард авторизации. Выключается только явно (BLAST_REQUIRE_AUTH=0) — например, чтобы
# погонять моки локально без Telegram-бота.
REQUIRE_AUTH = RUNTIME.require_auth

# DEV-ручки (`/api/dev/*`) умеют входить под ЛЮБЫМ аккаунтом и переписывать состояние,
# поэтому включаются явным флагом. В проде их быть не должно.
DEV_TOOLS = RUNTIME.dev_tools

# Открытые префиксы: сам вход, OAuth-возврат TikTok, dev-ручки и healthcheck.
PUBLIC_API_PREFIXES = ("/api/auth/", "/api/dev/", "/api/tiktok/auth", "/api/tiktok/callback", "/api/tiktok/status")
# Страницы, которые разлогиненному показывать можно
PUBLIC_PAGES = {"/login", "/register", "/not-found", "/error", "/blocked", "/legal/policy", "/legal/offer"}

# Что доступно ЗАБАНЕННОМУ аккаунту: весь блок входа (узнать причину, выйти, зайти под
# другой личностью) — и ничего больше. Сессию при бане не рвём специально: без неё экран
# блокировки не смог бы показать причину, и человек читал бы бан как сбой сервиса.
# Вход под другой личностью не «обход»: бан нового аккаунта проверяется на следующем же
# запросе, а тупик «почисти cookies, чтобы вообще войти» — это баг, а не защита.
# /api/dev/ban в этом списке только при включённых dev-ручках: иначе снять бан, которым
# сам же и проверял экран, было бы нечем — ручка блокировалась бы собственным баном.
BAN_ALLOWED_API = ("/api/auth/", "/api/dev/ban") if DEV_TOOLS else ("/api/auth/",)


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def bind_workspace(request: Request, call_next):
    """Привязать запрос к воркспейсу залогиненного юзера и закрыть данные без сессии.

    Раньше `/api/me` отдавал юзера вообще без сессии, а все `/app/*` открывались
    разлогиненным — данные были доступны кому угодно.
    """
    user_id = request.session.get("user_id")
    store.use_user(user_id)

    path = request.url.path
    if not DEV_TOOLS and path.startswith("/api/dev/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)

    if REQUIRE_AUTH and not user_id:
        if path.startswith("/api/") and path != "/api/mobile-upload" and not path.startswith(PUBLIC_API_PREFIXES):
            # code — машинный маркер: фронт уводит на /login только по нему, потому что
            # 401 может прилететь и от TikTok (протухший токен), и это не разлогин юзера
            return JSONResponse({"detail": "Требуется вход", "code": "auth_required"}, status_code=401)
        if (path == "/app" or path.startswith("/app/")) and path not in PUBLIC_PAGES:
            return RedirectResponse("/login", status_code=302)

    # Бан за переиспользованный аккаунт TikTok (см. fraud_guard). Проверяем до любой
    # полезной работы: забаненный не должен ни генерировать, ни выкладывать, ни платить.
    ban = fraud_guard.ban_status(user_id)
    if ban is not None:
        if path.startswith("/api/") and not path.startswith(BAN_ALLOWED_API):
            return JSONResponse({"detail": "Аккаунт заблокирован", "code": "account_banned", **ban}, status_code=403)
        if (path == "/app" or path.startswith("/app/")) and path not in PUBLIC_PAGES:
            return RedirectResponse("/blocked", status_code=302)

    blocked = security.guard(request)
    if blocked is not None:
        return blocked

    response = await call_next(request)
    security.issue_csrf_cookie(request, response)

    # Состояние живёт в памяти на время запроса, а в БД уезжает одним сбросом после
    # мутации: так не нужно помнить про save() в каждой из двух десятков функций стора.
    # Владельцев может быть двое: сессия появляется ВНУТРИ запроса (подтверждение входа
    # по токену, dev-login), и без второго id воркспейс нового юзера остался бы не сохранён.
    if request.method not in SAFE_METHODS:
        for owner in {user_id, request.session.get("user_id")}:
            persistence.flush_user(owner)
    return response


# ВАЖЕН порядок: add_middleware оборачивает уже собранный стек, поэтому SessionMiddleware
# регистрируется ПОСЛЕ bind_workspace — иначе request.session в нём ещё не существует.
# Секрет сессии — только из окружения. Фолбэк на случайный ключ означает, что без
# BLAST_SESSION_SECRET сессии не переживут рестарт: это заметно в деве и безопасно в проде
# (захардкоженный ключ позволял бы подделать cookie любому, кто видел исходники).
app.add_middleware(
    SessionMiddleware,
    secret_key=RUNTIME.session_secret,
    # https_only и same_site — из окружения: в проде обязательно BLAST_COOKIE_SECURE=1,
    # локально по http Secure-cookie браузер бы просто выбросил (см. security.cookie_*).
    same_site=security.cookie_samesite(),
    https_only=security.cookie_secure(),
)


@app.on_event("startup")
async def _restore_state() -> None:
    """Поднять сохранённое состояние. До этого рестарт стирал проекты, батчи и подписку."""
    persistence.load_all()
    auth_store.purge_expired_tokens()
    # Бот поднимается СРАЗУ, а не по первому «Войти через Telegram». Раньше между выдачей
    # ссылки и стартом поллинга был зазор, и первые нажатия /start просто не доходили.
    telegram_bot.ensure_started()
    if RUNTIME.backend == "production":
        # A production process that cannot reach its queue or object storage is
        # unhealthy.  Do not accept uploads and silently leave jobs stranded.
        await run_in_threadpool(_production_backend().healthcheck)
        await _billing_backend().init()
        await _billing_backend().healthcheck()
        await run_in_threadpool(security.healthcheck)
        await run_in_threadpool(telegram_bot.healthcheck)
        await run_in_threadpool(tiktok_token_store.healthcheck)


@app.on_event("shutdown")
async def _close_dependencies() -> None:
    if RUNTIME.backend == "production":
        from .production_backend import close_backend
        from .billing_backend import close_billing

        close_backend()
        await close_billing()


class ProjectPayload(BaseModel):
    name: str = Field(default="Новый проект")
    coverChoice: str = Field(default="auto")
    packageType: str = Field(default="BLAST")


class ProjectUpdatePayload(BaseModel):
    """PATCH проекта: любое поле опционально — присылают только то, что меняют."""
    name: str | None = Field(default=None, max_length=120)
    archived: bool | None = None


class PaymentPayload(BaseModel):
    packageType: str = "BLAST"
    projectId: str | None = None
    name: str | None = None
    coverChoice: str = "auto"
    recurrentAccepted: bool = False


class WizardSessionPayload(BaseModel):
    projectId: str | None = None
    stage: int = 1
    data: dict[str, Any] = Field(default_factory=dict)


class SubmitPayload(BaseModel):
    projectId: str | None = None
    stageData: dict[str, Any] = Field(default_factory=dict)
    videosToGenerate: int = 1
    idempotencyKey: str | None = None


class RatePayload(BaseModel):
    rating: str | int
    feedback: str | None = None


class TrackPayload(BaseModel):
    name: str
    props: dict[str, Any] = Field(default_factory=dict)


class TgStartPayload(BaseModel):
    """Имя/фамилия с формы регистрации (на входе не передаются).

    `mode` разводит вход и регистрацию: по «Войти» аккаунт не создаётся — если его нет,
    человек получает предложение зарегистрироваться, а не обязательный шаг «представься».
    """
    name: str | None = None
    surname: str | None = None
    mode: str = "register"


class ProfilePayload(BaseModel):
    name: str | None = None
    surname: str | None = None
    artistNick: str | None = None


class DeleteAccountPayload(BaseModel):
    confirmation: str


class TiktokPostPayload(BaseModel):
    projectId: str
    videoId: str
    caption: str = Field(default="", max_length=2200)
    privacy: str
    comments: bool = False
    duet: bool = False
    stitch: bool = False
    brandOrganic: bool = False
    brandContent: bool = False
    cover: bool = False
    coverFrame: int | None = Field(default=None, ge=0, le=7)
    coverTimestampMs: int = Field(default=0, ge=0)
    rights: bool = False
    # Оставлено для совместимости со старыми клиентами: TikTok Content Posting API
    # отложенной публикации не даёт, ролик уходит сразу — поле не используется.
    publishAt: str | None = None


class IterationPayload(BaseModel):
    videosToGenerate: int = Field(default=5, ge=1, le=50)
    testParameter: str = Field(default="subtitles", pattern="^(subtitles|hooks|background)$")


@app.exception_handler(404)
def custom_404(request: Request, exc: Exception):
    return JSONResponse({"detail": getattr(exc, "detail", "Not found")}, status_code=404)


# ------------------------- Mock API: auth -------------------------

def _sync_current_user(user: dict[str, Any]) -> None:
    """Открыть воркспейс залогиненного юзера и переключить на него контекст запроса.

    Новый аккаунт получает пустой воркспейс: демо-проекты остаются у демо-юзера.
    """
    email = user.get("email") or ""
    store.use_user(user["id"])
    space = store.workspace(user["id"], {
        "email": email,
        "name": user.get("name"),
        "surname": user.get("surname"),
        "artistNick": user.get("artistNick"),
        "tgVerified": True,
        "tgChatId": user.get("tgChatId"),
    })
    space.user["email"] = email
    # вход через Telegram: email ещё не задан, имя берём из профиля TG
    # Пустое имя не подменяем: по нему фронт покажет обязательный шаг «представься»
    space.user["name"] = user.get("name") or ""
    space.user["surname"] = user.get("surname") or space.user.get("surname") or ""
    if user.get("artistNick"):
        space.user["artistNick"] = user["artistNick"]
    if user.get("avatarUrl") and not space.user.get("avatarUrl"):
        space.user["avatarUrl"] = user["avatarUrl"]
    # Способов входа теперь два: tgVerified больше не «всегда True», иначе аккаунт,
    # заведённый через Google, выглядел бы подтверждённым в Telegram.
    space.user["authProvider"] = user.get("authProvider") or "telegram"
    space.user["tgVerified"] = bool(user.get("tgVerified"))
    space.user["tgChatId"] = user.get("tgChatId")
    # почта привязанного Google — по ней профиль показывает, подключён ли второй способ
    space.user["googleEmail"] = user.get("googleEmail") or (
        user.get("email") if user.get("authProvider") == "google" else None
    )


# Единственный вход — Telegram: /api/auth/tg-start выдаёт токен, юзер подтверждает его
# в боте (`/start <token>`), а `/api/auth/tg-verify` дожидается подтверждения и ставит сессию.
# Регистрации по email больше нет: почта и пароль в модели не участвуют.
@app.post("/api/auth/tg-start", tags=["auth"])
def api_tg_start(payload: TgStartPayload | None = None) -> dict[str, Any]:
    """Вход/регистрация через Telegram: личность определяет chat_id из `/start <token>`.

    Единственный способ входа — email больше не используется. С формы регистрации
    приходят имя и фамилия; они попадают в аккаунт при подтверждении токена.
    """
    profile = {
        "name": (payload.name or "").strip() if payload else "",
        "surname": (payload.surname or "").strip() if payload else "",
    }
    # purpose разводит два жеста: 'login' не заводит аккаунт, 'telegram' (регистрация) заводит
    purpose = "login" if payload and payload.mode == "login" else "telegram"
    tok = auth_store.issue_token("", purpose, profile)
    analytics.track("signup_started" if purpose == "telegram" else "login_started",
                    store.current_user_id(), {"withProfile": bool(profile.get("name"))})
    telegram_bot.ensure_started()
    return {
        "token": tok["token"],
        "deepLink": telegram_bot.deep_link(tok["token"]),
        "botConfigured": telegram_bot.configured(),
        "mock": not telegram_bot.configured(),
    }


@app.get("/api/auth/providers", tags=["auth"])
def api_auth_providers(request: Request) -> dict[str, Any]:
    """Какие способы входа реально доступны — фронт не должен показывать мёртвых кнопок.

    `googleBlocked` — отдельный флаг: ключи есть, но стране предлагать Google нельзя.
    Фронту важно различать «не настроено» и «запрещено», тексты у этого разные.
    """
    configured = google_auth.load().configured
    allowed = security.google_allowed(request)
    return {
        "telegram": True,
        "google": configured and allowed,
        "googleBlocked": configured and not allowed,
        "country": security.geo_country(request) or None,
    }


def _start_google(request: Request, *, link: bool) -> RedirectResponse:
    """Общий старт OAuth. `link=True` — привязка к текущему аккаунту, иначе вход.

    `state` кладём в сессию и сверяем на возврате — без него чужой сайт мог бы подсунуть
    свой код авторизации и привязать наш сеанс к своему аккаунту.
    """
    back = f"{_app_url()}/app/profile" if link else f"{_app_url()}/login"
    if not google_auth.load().configured:
        return RedirectResponse(f"{back}?auth=google_unavailable", status_code=302)
    # Гео-проверка и на старте, а не только на кнопке: адрес ручки видно в исходниках
    if not security.google_allowed(request):
        return RedirectResponse(f"{back}?auth=google_blocked", status_code=302)
    state = secrets.token_urlsafe(24)
    request.session["google_state"] = state
    request.session["google_link"] = link
    if not link:
        analytics.track("signup_started", store.current_user_id(), {"provider": "google"})
    return RedirectResponse(google_auth.authorize_url(state), status_code=302)


@app.get("/api/auth/google", tags=["auth"])
def api_google_auth(request: Request) -> RedirectResponse:
    """Старт входа через Google: уводим на экран выбора аккаунта."""
    return _start_google(request, link=False)


@app.get("/api/auth/google/link", tags=["auth"])
def api_google_link(request: Request) -> RedirectResponse:
    """Привязать Google к уже открытому аккаунту (кнопка в профиле)."""
    if not request.session.get("user_id"):
        return RedirectResponse(f"{_app_url()}/login", status_code=302)
    return _start_google(request, link=True)


@app.delete("/api/auth/google/link", tags=["auth"])
def api_google_unlink(request: Request) -> dict[str, Any]:
    """Отвязать Google от аккаунта."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail={"detail": "Требуется вход", "code": "auth_required"})
    try:
        user = auth_store.unlink_google(user_id)
    except ValueError as exc:
        # last_provider — единственный способ войти, отвязывать нельзя
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Реестр и воркспейс — две копии профиля: /api/me читает вторую, и без пересинка
    # отвязанная почта продолжала показываться в профиле как подключённая.
    _sync_current_user(user)
    return {"ok": True}


@app.get("/api/auth/google/callback", tags=["auth"])
def api_google_callback(request: Request, code: str | None = None, state: str | None = None,
                        error: str | None = None) -> RedirectResponse:
    """Возврат от Google: сверяем state и либо входим, либо привязываем к текущему аккаунту."""
    expected = request.session.pop("google_state", None)
    # Один колбэк на оба сценария: адрес redirect_uri жёстко зашит в консоли Google,
    # заводить второй под привязку — лишняя настройка, которую забудут сделать.
    linking = bool(request.session.pop("google_link", False))
    back = f"{_app_url()}/app/profile" if linking else f"{_app_url()}/login"
    if error:
        # Человек нажал «отмена» на экране Google — это не сбой, а решение
        return RedirectResponse(f"{back}?auth=denied", status_code=302)
    # Сравниваем БАЙТЫ: compare_digest на строках с не-ASCII бросает TypeError, то есть
    # любой мог получить 500 вместо честного редиректа, прислав кириллицу в state.
    if not code or not state or not expected or not secrets.compare_digest(state.encode(), expected.encode()):
        return RedirectResponse(f"{back}?auth=state", status_code=302)
    try:
        profile = google_auth.profile_from_tokens(google_auth.exchange_code(code))
    except google_auth.GoogleAuthError:
        return RedirectResponse(f"{back}?auth=error", status_code=302)

    if linking:
        linked_id = request.session.get("user_id")
        if not linked_id:
            return RedirectResponse(f"{_app_url()}/login", status_code=302)
        try:
            linked = auth_store.link_google(linked_id, profile)
        except ValueError as exc:
            # google_taken — почтой уже пользуется другой аккаунт; склеивать их нельзя:
            # у каждого свои проекты и своя подписка
            reason = "google_taken" if str(exc) == "google_taken" else "error"
            return RedirectResponse(f"{back}?auth={reason}", status_code=302)
        _sync_current_user(linked)
        persistence.flush_user(linked["id"])
        return RedirectResponse(f"{back}?auth=google_linked", status_code=302)

    try:
        user = auth_store.get_or_create_google_user(profile)
    except ValueError:
        return RedirectResponse(f"{back}?auth=error", status_code=302)

    request.session["user_id"] = user["id"]
    _sync_current_user(user)
    analytics.track("signup_completed", user["id"], {"source": "google"})
    # Колбэк — GET, а сброс состояния в middleware висит на мутирующих методах:
    # без явного вызова воркспейс нового аккаунта не попал бы в БД до первой правки.
    persistence.flush_user(user["id"])
    return RedirectResponse(f"{_app_url()}/app", status_code=302)


@app.post("/api/auth/logout", tags=["auth"])
def api_logout(request: Request) -> dict[str, Any]:
    request.session.clear()
    return {"ok": True}


@app.get("/api/auth/ban-status", tags=["auth"])
def api_ban_status(request: Request) -> dict[str, Any]:
    """Причина блокировки для экрана /blocked.

    Единственная ручка, кроме выхода, которая забаненному отвечает 200: экран блокировки
    должен объяснить, за что именно, иначе человек читает бан как поломку сервиса.
    """
    ban = fraud_guard.ban_status(request.session.get("user_id"))
    return ban or {"banned": False, "reason": None, "bannedAt": None}


@app.get("/api/auth/tg-verify", tags=["auth"])
def api_tg_verify(request: Request, token: str | None = None) -> dict[str, Any]:
    if not token:
        raise HTTPException(status_code=422, detail="token обязателен")
    rec = auth_store.token_status(token)
    if not rec:
        return {"verified": False}
    # Вход по «Войти» на несуществующий аккаунт: бот отметил токен, вход не состоялся.
    # Фронт по этому флагу предлагает регистрацию вместо экрана «представься».
    if rec.get("noAccount"):
        return {"verified": False, "noAccount": True}
    # Дев без бота: сами подтверждаем после 2 опросов, чтобы флоу проходился локально.
    # allow_create=True здесь обязателен: chat_id нет, найти существующий аккаунт нечем.
    if not telegram_bot.configured() and not rec["verified"]:
        rec["polls"] = rec.get("polls", 0) + 1
        if rec["polls"] >= 2:
            auth_store.confirm_token(token, auth_store.DEV_CHAT_ID, allow_create=True)
    if rec["verified"]:
        user = auth_store.get_user(rec["email"])
        if user:
            request.session["user_id"] = user["id"]
            _sync_current_user(user)
            analytics.track("signup_completed", user["id"], {"source": "telegram"})
            return {"verified": True, "user": {"id": user["id"], "email": user["email"], "name": user["name"]}}
    return {"verified": False}


@app.get("/api/me", tags=["profile"])
async def api_me() -> dict[str, Any]:
    data = store.get_user_bundle()
    if RUNTIME.backend == "production":
        try:
            await _sync_billing_bundle(data)
        except HTTPException:
            # Google-only accounts can browse and link Telegram.  Payment and
            # generation remain explicitly blocked by _telegram_chat_id().
            data["billingLinkRequired"] = True
        except Exception as exc:
            raise _production_error(exc) from exc
        # Не показываем старую persisted mock-запись как реальное подключение,
        # если TikTok credentials отключены на production-инстансе.
        if not tiktok_config.load().configured:
            data["tiktok"] = None
    # Экран ожидания обещает «пришлём в Telegram» — обещать это можно только когда бот
    # реально настроен И у юзера есть привязанный чат. Иначе фронт молчит про уведомления.
    data["telegramNotifications"] = bool(
        telegram_bot.configured() and auth_store.chat_id_for_user(store.current_user_id() or "")
    )
    data["mock"] = RUNTIME.backend == "mock"
    data["capabilities"] = {
        "customSources": True,
        # Кандидаты дропа есть в обоих режимах: в моке — фикстура, в проде —
        # `POST /hook/analyze` оркестратора (та же ручка, что у бота).
        "analyzedDrops": True,
        "remoteCompositePreviews": RUNTIME.backend == "mock",
        "subscriptionBonuses": True,
    }
    return data


# ------------------------- Projects -------------------------

@app.get("/api/projects", tags=["projects"])
def api_projects() -> dict[str, Any]:
    return store.list_projects()


@app.post("/api/projects", tags=["projects"])
def api_create_project(payload: ProjectPayload) -> dict[str, Any]:
    project = store.create_project(payload.name, payload.packageType, payload.coverChoice)
    analytics.track("project_created", store.current_user_id(), {"projectId": project["id"]})
    return {"project": project, "redirectTo": f"/app/projects/{project['id']}", "mock": RUNTIME.backend == "mock"}


@app.get("/api/projects/{project_id}", tags=["projects"])
def api_project(project_id: str) -> dict[str, Any]:
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project": project, "mock": RUNTIME.backend == "mock"}


@app.patch("/api/projects/{project_id}", tags=["projects"])
def api_update_project(project_id: str, payload: ProjectUpdatePayload) -> dict[str, Any]:
    """Переименование и архив. Раньше ошибка в названии оставалась навсегда,
    а лента проектов со временем зарастала мусором."""
    project = None
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Название не может быть пустым")
        project = store.rename_project(project_id, name)
    if payload.archived is not None:
        project = store.set_project_archived(project_id, payload.archived)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project": project, "mock": RUNTIME.backend == "mock"}


@app.delete("/api/projects/{project_id}", tags=["projects"])
def api_delete_project(project_id: str) -> dict[str, Any]:
    """Удаление вместе с батчами. Лимит треков не возвращается — он считается
    по загруженным трекам, а не по проектам."""
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    from . import upload_store
    owner = store.current_user_id()
    assets = upload_store.assets(owner, project_id)
    try:
        if RUNTIME.backend == "production":
            backend = _production_backend()
            for asset in assets:
                backend.delete_uploaded_asset(asset["s3Key"])
        else:
            for asset in assets:
                (SOURCE_DIR / Path(asset["s3Key"]).name).unlink(missing_ok=True)
    except Exception as exc:
        raise _production_error(exc) from exc
    upload_store.remove_project(owner, project_id)
    store.delete_project(project_id)
    analytics.track("project_deleted", store.current_user_id(), {"projectId": project_id})
    return {"ok": True, "mock": RUNTIME.backend == "mock"}


# ------------------------- Mock API: payments -------------------------

@app.post("/api/payments/create-order", tags=["payments"])
async def api_create_order(payload: PaymentPayload) -> dict[str, Any]:
    if RUNTIME.backend == "production":
        tg_id = _telegram_chat_id()
        try:
            order = await _billing_backend().create_order(
                tg_id=tg_id,
                package_type=payload.packageType,
                email=str(store.USER.get("email") or store.USER.get("googleEmail") or ""),
                recurrent_accepted=payload.recurrentAccepted,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "payment_init_failed", "message": str(exc)},
            ) from exc
        # T-Bank Init is an external side effect.  A best-effort analytics
        # write must never turn a successfully created payment into a 500:
        # the browser would retry and create a second order.  Persistence
        # failures remain visible in logs and are repaired separately.
        try:
            analytics.track(
                "payment_link_created",
                store.current_user_id(),
                {"tier": payload.packageType.upper(), "orderId": order["orderId"]},
            )
        except Exception:
            logger.exception("payment_link_created analytics write failed order_id=%s", order["orderId"])
        return {**order, "project": None, "mock": False}

    order_id = f"order_{uuid4().hex[:8]}"
    project = None
    if payload.name:
        project = store.create_project(payload.name, payload.packageType, payload.coverChoice)
    # Мок: «оплата» сразу активирует купленный план (подписка → tier с его лимитами)
    store.activate_plan(payload.packageType)
    analytics.track("plan_purchased", store.current_user_id(), {"tier": payload.packageType.upper()})
    return {
        "orderId": order_id,
        "paymentUrl": f"/app/pricing?mockPaid={order_id}",
        "project": project,
        "message": "Mock payment link generated. Webhook is not required in local mode.",
        "mock": True,
    }


@app.post("/api/payments/claim-bonus", tags=["payments"])
async def api_claim_bonus() -> dict[str, Any]:
    """Забрать бонус со шкалы месяцев: +1 трек, за третий месяц — снятие лимита треков."""
    if RUNTIME.backend == "production":
        try:
            snapshot = await _billing_backend().claim_bonus(_telegram_chat_id())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Бонус ещё не заработан") from exc
        data = await _sync_billing_bundle(store.get_user_bundle())
        subscription = data["subscription"]
        analytics.track("bonus_claimed", store.current_user_id(), {"claimed": snapshot["bonusesClaimed"]})
        return {"ok": True, "subscription": subscription, "mock": False}
    try:
        subscription = store.claim_bonus()
    except ValueError:
        raise HTTPException(status_code=409, detail="Бонус ещё не заработан") from None
    analytics.track("bonus_claimed", store.current_user_id(), {"claimed": subscription.get("bonusesClaimed")})
    return {"ok": True, "subscription": subscription, "mock": True}


@app.post("/api/payments/webhook", tags=["payments"])
def api_payment_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    if RUNTIME.backend == "production":
        # Production notifications are verified and processed by the public
        # bot's existing T-Bank endpoint configured in TBANK_NOTIFY_URL.
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True, "signatureVerified": True, "received": payload, "mock": True}


@app.post("/api/payments/cancel-sub", tags=["payments"])
async def api_cancel_sub(immediate: bool = False) -> dict[str, Any]:
    """Отмена подписки. По умолчанию — с конца оплаченного периода.

    Отменить можно в любой момент, в том числе при неудачном списании: право на отмену —
    обязательное условие оферты, и запирать юзера в `past_due` нельзя.
    """
    if RUNTIME.backend == "production":
        if immediate:
            raise HTTPException(status_code=422, detail="Immediate cancellation is not supported")
        if not await _billing_backend().cancel(_telegram_chat_id()):
            raise HTTPException(status_code=409, detail="Active subscription not found")
        data = await _sync_billing_bundle(store.get_user_bundle())
        sub = data["subscription"]
    else:
        sub = store.cancel_subscription(at_period_end=not immediate)
    analytics.track("subscription_canceled", store.current_user_id(), {"tier": sub["tier"], "immediate": immediate})
    return {"ok": True, "subscription": sub, "mock": RUNTIME.backend == "mock"}


@app.post("/api/payments/retry", tags=["payments"])
async def api_payment_retry() -> dict[str, Any]:
    """Повторить списание после неудачной оплаты (в моке — всегда успешно).

    Реальный провайдер здесь вернёт ссылку на оплату; контракт ответа не изменится.
    """
    if RUNTIME.backend == "production":
        data = await _sync_billing_bundle(store.get_user_bundle())
        tier = str(data["subscription"].get("tier") or "")
        if tier != "BLAST":
            raise HTTPException(status_code=409, detail="No BLAST subscription to retry")
        order = await _billing_backend().create_order(
            tg_id=_telegram_chat_id(),
            package_type=tier,
            email=str(store.USER.get("email") or store.USER.get("googleEmail") or ""),
            recurrent_accepted=True,
        )
        return {"ok": True, "subscription": data["subscription"], "paymentUrl": order["paymentUrl"], "mock": False}
    sub = store.mark_payment_ok()
    return {"ok": True, "subscription": sub, "paymentUrl": None, "mock": True}


@app.post("/api/payments/resume", tags=["payments"])
async def api_payment_resume() -> dict[str, Any]:
    """Отменить запланированную отмену — вернуть автопродление."""
    if RUNTIME.backend == "production":
        if not await _billing_backend().resume(_telegram_chat_id()):
            raise HTTPException(status_code=409, detail="Canceled recurrent subscription not found")
        data = await _sync_billing_bundle(store.get_user_bundle())
        return {"ok": True, "subscription": data["subscription"], "mock": False}
    sub = store.ws().subscription
    if sub.get("cancelAtPeriodEnd"):
        sub.update({"cancelAtPeriodEnd": False, "billingStatus": "active"})
    return {"ok": True, "subscription": sub, "mock": True}


# ------------------------- Mock API: wizard -------------------------

@app.post("/api/wizard/upload-track", tags=["wizard"])
async def api_upload_track(file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read()
    # Проверяем СОДЕРЖИМОЕ, а не заявленный content_type: последний приходит от клиента
    # и под видом mp3 можно было залить что угодно.
    security.check_audio(content, max_mb=200)
    # Лимит треков — то, чем реально ограничен тариф (роликов на подключённом TikTok безлимит).
    # Раньше tracksUsed не рос вообще и лимит не работал ни на одном плане.
    audio_hash = hashlib.sha256(content).hexdigest()
    left = store.tracks_left()
    if RUNTIME.backend == "production":
        try:
            allowed = await _billing_backend().can_upload_track(_telegram_chat_id(), audio_hash)
        except HTTPException:
            raise
        except Exception as exc:
            raise _production_error(exc) from exc
    else:
        allowed = left is None or left > 0
    if not allowed:
        analytics.track("limit_hit", store.current_user_id(), {"limit": "tracks"})
        raise HTTPException(
            status_code=402,
            detail=f"Лимит треков исчерпан ({store.ws().subscription['tracksTotal']}). Расширь тариф, чтобы взять новый трек.",
        )
    # Расширение — только из белого списка: имя файла приходит от клиента, и в него
    # можно положить «..» или исполняемый суффикс.
    ext = security.safe_extension(file.filename, {".mp3", ".wav", ".m4a", ".ogg", ".flac"}, ".mp3")
    safe_name = f"{uuid4().hex}{ext}"
    display_name = security.sanitize_filename(file.filename, safe_name)
    if RUNTIME.backend == "production":
        try:
            uploaded = await run_in_threadpool(
                _production_backend().upload_track,
                content=content,
                user_id=store.current_user_id(),
                filename=display_name,
                content_type=file.content_type,
            )
        except Exception as exc:  # dependency failure is explicit, never a local-file fallback
            raise _production_error(exc) from exc
        track = store.save_track(
            display_name,
            s3_url=uploaded["s3_url"],
            playback_url=uploaded["playback_url"],
            audio_hash=audio_hash,
        )
    else:
        target = UPLOAD_DIR / safe_name
        target.write_bytes(content)
        track = store.save_track(display_name, target, audio_hash=audio_hash)
    analytics.track("track_uploaded", store.current_user_id(), {"trackId": track["id"]})
    return {"track": track, "tracksLeft": store.tracks_left(), "mock": RUNTIME.backend == "mock"}


def _upload_project(project_id: str) -> str:
    if not project_id or not any(p["id"] == project_id for p in store.list_projects()["projects"]):
        raise HTTPException(404, detail="Проект не найден")
    return project_id


@app.post("/api/wizard/upload-source", tags=["wizard"])
async def api_upload_source(file: UploadFile = File(...), projectId: str = "", format: str = "9:16") -> dict[str, Any]:
    from .source_uploads import upload
    _upload_project(projectId)
    if format not in {"9:16", "16:9"}:
        raise HTTPException(422, detail="Выберите формат 9:16 или 16:9")
    source = await upload(file, user_id=store.current_user_id(), project_id=projectId, kind="source", format=format,
                          local_dir=SOURCE_DIR, backend=_production_backend() if RUNTIME.backend == "production" else None)
    return {"source": source, "mock": RUNTIME.backend == "mock"}


@app.get("/api/wizard/sources", tags=["wizard"])
def api_sources(projectId: str = "") -> dict[str, Any]:
    from . import upload_store
    _upload_project(projectId)
    sources = [item for item in upload_store.assets(store.current_user_id(), projectId) if item["kind"] == "source"]
    if RUNTIME.backend == "production":
        for item in sources:
            item["localUrl"] = _production_backend()._preview_url(item["s3Key"], filename=item["name"])
    return {"sources": sources}


@app.delete("/api/wizard/sources/{source_id}", tags=["wizard"])
def api_delete_source(source_id: str) -> dict[str, Any]:
    from . import upload_store
    owner = store.current_user_id()
    asset = next((item for item in upload_store.assets(owner) if item["id"] == source_id), None)
    if not asset:
        raise HTTPException(404, detail="Исходник не найден")
    if RUNTIME.backend == "production":
        _production_backend().delete_uploaded_asset(asset["s3Key"])
    else:
        path = SOURCE_DIR / Path(asset["s3Key"]).name
        path.unlink(missing_ok=True)
    upload_store.remove(owner, source_id)
    return {"ok": True}


@app.post("/api/wizard/upload-hook-sound", tags=["wizard"])
async def api_upload_hook_sound(file: UploadFile = File(...)) -> dict[str, Any]:
    return await _upload_warmup(file, "warmup-audio")


@app.post("/api/wizard/upload-hook-video", tags=["wizard"])
async def api_upload_hook_video(file: UploadFile = File(...)) -> dict[str, Any]:
    return await _upload_warmup(file, "warmup-video")


async def _upload_warmup(file: UploadFile, kind: str) -> dict[str, Any]:
    from .source_uploads import upload
    asset = await upload(file, user_id=store.current_user_id(), project_id="", kind=kind, format=None,
                         local_dir=SOURCE_DIR, backend=_production_backend() if RUNTIME.backend == "production" else None)
    return {**asset, "url": asset["s3Key"], "playbackUrl": asset["localUrl"], "mock": RUNTIME.backend == "mock"}


@app.post("/api/wizard/upload-link", tags=["wizard"])
def api_upload_link(projectId: str = "", format: str = "9:16") -> dict[str, Any]:
    from . import upload_store
    from io import BytesIO
    import qrcode
    import qrcode.image.svg
    _upload_project(projectId)
    try:
        token, expires = upload_store.make_link(store.current_user_id(), projectId, format)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    # Fragment is never sent in HTTP access logs or Referer headers.
    url = f"{RUNTIME.app_url.rstrip('/')}/upload/#{token}"
    svg = BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage).save(svg)
    return {"url": url, "expiresAt": expires, "qrSvg": svg.getvalue().decode("utf-8")}


def _phone_link(request: Request, consume: bool = False) -> dict[str, Any]:
    from . import upload_store
    try:
        link = upload_store.link(request.headers.get("X-Upload-Token", ""), consume=consume)
    except ValueError as exc:
        raise HTTPException(410, detail=str(exc)) from exc
    if fraud_guard.ban_status(link["userId"]) is not None:
        raise HTTPException(403, detail="Загрузка недоступна")
    return link


@app.get("/api/mobile-upload")
def api_phone_status(request: Request) -> dict[str, Any]:
    link = _phone_link(request)
    return {key: link[key] for key in ("format", "expiresAt", "remaining")}


@app.post("/api/mobile-upload")
async def api_phone_upload(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    from . import upload_store
    from .source_uploads import upload
    token = request.headers.get("X-Upload-Token", "")
    link = _phone_link(request, consume=True)
    try:
        asset = await upload(file, user_id=link["userId"], project_id=link["projectId"], kind="source", format=link["format"],
                             local_dir=SOURCE_DIR, backend=_production_backend() if RUNTIME.backend == "production" else None)
    except Exception:
        upload_store.restore_link(token)
        raise
    return {"name": asset["name"], "uploaded": True}


@app.get("/api/wizard/previous-track", tags=["wizard"])
def api_previous_track() -> dict[str, Any]:
    return {"track": store.previous_track(), "mock": RUNTIME.backend == "mock"}


@app.get("/api/wizard/drops", tags=["wizard"])
async def api_drops(clipFrom: str = "", clipTo: str = "") -> dict[str, Any]:
    """Кандидаты дропа для выбранного отрывка — то же, что показывает бот.

    Бот не хранит три фиксированных тайминга: он зовёт `POST /hook/analyze`
    оркестратора (там librosa и `analyze_focus_clip`) и рисует топ-3 кандидата с
    процентом уверенности, помечая первый как лучший — см.
    `services/tg_bot_botapi/app.py::_ask_hook_drop`. Здесь то же самое: сайт —
    такой же тонкий клиент этой ручки.

    Окно отрывка приходит от визарда в 'mm:ss' (тот же формат, что уходит в
    render_job через `render_job._segment`). Без окна анализировать нечего:
    кандидаты ищутся ВНУТРИ отрывка, а не по всему треку.
    """
    if RUNTIME.backend != "production":
        return {"status": "COMPLETED", "bpm": 142, "drops": store.DROPS, "mock": True}

    start = render_job_builder.mmss_seconds(clipFrom)
    end = render_job_builder.mmss_seconds(clipTo)
    if start is None or end is None or end <= start:
        # Не 5xx: это нормальное состояние визарда до выбора отрывка. Фронт
        # показывает «выбери отрывок», а не «сервер прилёг».
        return {"status": "NEEDS_CLIP", "bpm": 0, "drops": [], "mock": False}

    track = store.previous_track() or {}
    audio_s3_url = str(track.get("s3Key") or "").strip()
    if not audio_s3_url:
        return {"status": "NEEDS_TRACK", "bpm": 0, "drops": [], "mock": False}

    try:
        result = await run_in_threadpool(
            _production_backend().analyze_hook,
            audio_s3_url=audio_s3_url,
            clip_start_sec=start,
            clip_end_sec=end,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _production_error(exc) from exc

    # Показываем топ-3, как бот: остальной пул нужен только батарее.
    drops = []
    for index, candidate in enumerate(result.get("drop_candidates") or []):
        seconds = float(candidate.get("t"))
        drops.append(
            {
                "time": f"{int(seconds // 60):02d}:{int(seconds % 60):02d}",
                "seconds": seconds,
                "best": index == 0,
                "confidence": float(candidate.get("confidence") or 0.0),
            }
        )
        if len(drops) == 3:
            break
    return {"status": "COMPLETED", "bpm": float(result.get("bpm") or 0.0), "drops": drops, "mock": False}


@app.get("/api/wizard/vibes", tags=["wizard"])
def api_vibes(plane: str = "vibes") -> dict[str, Any]:
    """Примеры футажа выбранного ПЛАНА подбора.

    Планов три, как у бота: `vibes` (вертикальные 9:16), `cine16x9` и `films`.
    Записи каталога без поля `plane` считаются вертикальными — так фикстуры мока
    и любые старые каталоги остаются валидными.
    """
    if RUNTIME.backend == "production":
        try:
            vibes = _production_backend().preview_catalog("footage")
        except Exception as exc:
            raise _production_error(exc) from exc
    else:
        vibes = store.VIBES
    wanted = str(plane or "vibes").strip() or "vibes"
    vibes = [item for item in vibes if str(item.get("plane") or "vibes") == wanted]
    return {"status": "COMPLETED", "vibes": vibes, "mock": RUNTIME.backend == "mock"}


@app.get("/api/wizard/photos", tags=["wizard"])
def api_photos() -> dict[str, Any]:
    if RUNTIME.backend == "production":
        try:
            photos = _production_backend().preview_catalog("photo")
        except Exception as exc:
            raise _production_error(exc) from exc
        return {"status": "COMPLETED", "photos": photos, "mock": False}
    return {"status": "COMPLETED", "photos": store.PHOTOS, "mock": True}


@app.get("/api/wizard/fx-previews", tags=["wizard"])
def api_fx_previews() -> dict[str, Any]:
    if RUNTIME.backend == "production":
        try:
            return {"previews": _production_backend().preview_catalog("fx"), "mock": False}
        except Exception as exc:
            raise _production_error(exc) from exc
    return {"previews": [], "mock": True}


@app.get("/api/wizard/subtitle-styles", tags=["wizard"])
def api_subtitle_styles() -> dict[str, Any]:
    if RUNTIME.backend == "production":
        try:
            styles = _production_backend().preview_catalog("subtitle")
        except Exception as exc:
            raise _production_error(exc) from exc
        return {"status": "COMPLETED", "styles": styles, "mock": False}
    return {"status": "COMPLETED", "styles": store.SUBTITLE_STYLES, "mock": True}


@app.get("/api/wizard/session", tags=["wizard"])
def api_get_wizard_session() -> dict[str, Any]:
    return {"session": store.get_wizard_session(), "mock": RUNTIME.backend == "mock"}


@app.post("/api/wizard/session", tags=["wizard"])
def api_save_wizard_session(payload: WizardSessionPayload) -> dict[str, Any]:
    return {"session": store.set_wizard_session(payload.model_dump()), "mock": RUNTIME.backend == "mock"}


@app.post("/api/wizard/submit", tags=["wizard"])
async def api_submit_wizard(payload: SubmitPayload) -> dict[str, Any]:
    # Трек и текст — обязательные вводные: без них рендерить lyric-video нечего.
    # Фронт не пускает дальше этапа «Трек», но ручка не должна полагаться на это.
    stage_data = payload.stageData or {}
    if not (stage_data.get("track") or {}):
        raise HTTPException(status_code=422, detail="Не выбран трек")
    if not str(stage_data.get("lyrics") or "").strip():
        raise HTTPException(status_code=422, detail="Не заполнен текст трека")

    projects = store.list_projects()["projects"]
    requested = payload.projectId if any(p["id"] == payload.projectId for p in projects) else None
    project_id = (
        requested
        or store.current_project_id()
        or (projects[0]["id"] if projects else store.create_project("Новый проект")["id"])
    )

    from . import upload_store
    bg = stage_data.get("background") or {}
    owned = upload_store.assets(store.current_user_id())
    by_id = {item["id"]: item for item in owned}
    raw_plans = bg.get("sourceVideos") or []
    if not raw_plans and bg.get("uploads"):
        raw_plans = [{"id": "source-video-legacy", "format": bg.get("sourceFormat") or "9:16", "sourceIds": bg["uploads"]}]
    plans: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    seen_plan_ids: set[str] = set()
    seen_source_ids: set[str] = set()
    if not isinstance(raw_plans, list):
        raise HTTPException(422, detail="Пересоберите личные видео: набор должен быть списком")
    for raw_plan in raw_plans:
        if not isinstance(raw_plan, dict):
            raise HTTPException(422, detail="Пересоберите личные видео: найден повреждённый пункт")
        plan_id = str(raw_plan.get("id") or "")
        source_ids = [str(value) for value in raw_plan.get("sourceIds") or []]
        plan_format = str(raw_plan.get("format") or "")
        if (not plan_id or plan_id in seen_plan_ids or plan_format not in {"9:16", "16:9"}
                or not source_ids or len(source_ids) != len(set(source_ids))):
            raise HTTPException(422, detail="Пересоберите личные видео: найден пустой или повреждённый набор")
        if seen_source_ids.intersection(source_ids):
            raise HTTPException(422, detail="Один исходник нельзя добавить в несколько личных видео")
        seen_plan_ids.add(plan_id)
        seen_source_ids.update(source_ids)
        plan_assets = []
        for source_id in source_ids:
            item = by_id.get(source_id)
            if not item or item["kind"] != "source" or item["projectId"] != project_id:
                raise HTTPException(422, detail="Исходник не принадлежит этому проекту. Загрузите файл заново.")
            if item.get("format") != plan_format:
                raise HTTPException(422, detail="В одном личном видео нельзя смешивать 9:16 и 16:9")
            plan_assets.append(item)
        plans.append({"id": plan_id, "format": plan_format, "sourceIds": source_ids})
        sources.extend(plan_assets)
    bg["sourceVideos"] = plans
    bg["uploads"] = list(dict.fromkeys(item["id"] for item in sources))
    bg["sourceAssets"] = sources
    by_url = {item["s3Key"]: item for item in owned}
    hooks = stage_data.get("hooks") or {}
    configs = hooks.get("configs") or {}
    for family in ("sound", "warmup"):
        cfg = configs.get(family)
        if not cfg: continue
        video = cfg.get("warmupKind") == "video"
        url = cfg.get("videoUrl" if video else "soundUrl")
        asset = by_url.get(url)
        if not asset or asset["kind"] != ("warmup-video" if video else "warmup-audio"):
            raise HTTPException(422, detail="Загрузите файл прогрева заново")
        if video:
            cfg.update(videoDuration=asset["duration"], videoWidth=asset["width"], videoHeight=asset["height"], videoHasAudio=asset["hasAudio"])
        else:
            cfg["soundDuration"] = asset["duration"]
    if RUNTIME.backend == "production":
        try:
            _production_backend().validate_stage(stage_data)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    # Лимит роликов производный: 5 на триале без TikTok, безлимит (None) — с подключённым
    credits_total = store.video_limit()
    # Повтор по тому же ключу возвращает уже созданный джоб — и не должен упираться в лимит
    replay = payload.idempotencyKey and payload.idempotencyKey in store.JOB_IDEMPOTENCY
    if RUNTIME.backend == "mock" and credits_total is not None and not replay:
        credits_left = credits_total - store.SUBSCRIPTION["creditsUsed"]
        if payload.videosToGenerate > credits_left:
            analytics.track("limit_hit", store.current_user_id(), {"limit": "videos", "left": credits_left})
            raise HTTPException(status_code=402, detail=f"Доступно {credits_left} генераций")
    job = store.create_job(
        project_id,
        stage_data,
        payload.videosToGenerate,
        payload.idempotencyKey,
        enqueue_mock=RUNTIME.backend == "mock",
    )
    if RUNTIME.backend == "production":
        live_job = store.JOBS[job["id"]]
        try:
            _production_backend().validate_job(live_job)
        except (ValueError, RuntimeError) as exc:
            store.rollback_job_creation(live_job["id"])
            raise HTTPException(422, detail=str(exc)) from exc
        tg_id = _telegram_chat_id()
        track_hash = str((stage_data.get("track") or {}).get("audioHash") or "")
        if not track_hash:
            store.rollback_job_creation(live_job["id"])
            raise HTTPException(status_code=422, detail="Uploaded track has no content hash")
        try:
            await _billing_backend().reserve(tg_id, live_job["id"], len(live_job.get("videos") or []))
            await _billing_backend().consume_track(tg_id, track_hash)
            await run_in_threadpool(_production_backend().enqueue_job, live_job)
        except Exception as exc:
            from .billing_backend import InsufficientCredits, TrackQuotaExhausted

            if isinstance(exc, InsufficientCredits):
                store.rollback_job_creation(live_job["id"])
                raise HTTPException(status_code=402, detail=f"Доступно {exc.available} генераций") from exc
            if isinstance(exc, TrackQuotaExhausted):
                await _billing_backend().refund(
                    tg_id, live_job["id"], len(live_job.get("videos") or [])
                )
                store.rollback_job_creation(live_job["id"])
                raise HTTPException(status_code=402, detail="Лимит уникальных треков исчерпан") from exc
            partial = any(
                video.get("orchestratorJobId")
                for video in live_job.get("videos", [])
            )
            if partial:
                live_job["enqueueError"] = str(exc)[:2000]
                persistence.save_job(live_job["id"])
            else:
                await _billing_backend().refund(
                    tg_id, live_job["id"], len(live_job.get("videos") or [])
                )
                store.rollback_job_creation(live_job["id"])
            raise _production_error(exc) from exc
        live_job.pop("enqueueError", None)
        persistence.save_job(live_job["id"])
        job = store.get_job(live_job["id"]) or live_job
    analytics.track("generation_started", store.current_user_id(), {"jobId": job["id"], "videos": job["versions"], "projectId": project_id})
    return {"job": job, "redirectTo": f"/app/processing/{job['id']}", "mock": RUNTIME.backend == "mock"}


# ------------------------- Mock API: preview -------------------------

@app.get("/api/preview/composite", tags=["preview"])
def api_preview_composite(style: str = "Impulse", hook: str = "none") -> dict[str, Any]:
    if RUNTIME.backend == "production":
        raise HTTPException(
            status_code=501,
            detail={"code": "preview_not_configured", "message": "Composite preview is not configured."},
        )
    return {
        "previewUrl": f"{store.BASE_S3}/previews/composite/{style.lower()}-{hook}.mp4",
        "style": style,
        "hook": hook,
        "mock": True,
    }


# ------------------------- Mock API: jobs -------------------------

@app.get("/api/jobs/active", tags=["jobs"])
async def api_active_job() -> dict[str, Any]:
    job = store.active_job()
    if job and RUNTIME.backend == "production":
        try:
            live_job = store.JOBS[job["id"]]
            await run_in_threadpool(_production_backend().sync_job, live_job)
            await _refund_terminal_failures(live_job)
            persistence.save_job(live_job["id"])
            job = store.get_job(live_job["id"])
        except Exception as exc:
            raise _production_error(exc) from exc
    return {"job": job, "mock": RUNTIME.backend == "mock"}


@app.get("/api/jobs/{job_id}", tags=["jobs"])
async def api_job(job_id: str) -> dict[str, Any]:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if RUNTIME.backend == "production":
        try:
            live_job = store.JOBS[job_id]
            await run_in_threadpool(_production_backend().sync_job, live_job)
            await _refund_terminal_failures(live_job)
            persistence.save_job(job_id)
            job = store.get_job(job_id) or live_job
        except Exception as exc:
            raise _production_error(exc) from exc
    return {"job": job, "mock": RUNTIME.backend == "mock"}


async def _refund_terminal_failures(job: dict[str, Any]) -> None:
    if job.get("status") != "FAILED" or job.get("failedCreditsRefunded"):
        return
    failed = sum(1 for video in job.get("videos", []) if video.get("status") == "FAILED")
    if failed:
        await _billing_backend().refund(_telegram_chat_id(), job["id"], failed)
    job["failedCreditsRefunded"] = failed


@app.post("/api/jobs/{job_id}/rate", tags=["jobs"])
def api_rate_job(job_id: str, payload: RatePayload) -> dict[str, Any]:
    job = store.JOBS.get(job_id)
    if not job or job.get("userId") != store.current_user_id():
        raise HTTPException(status_code=404, detail="Job not found")
    job["rating"] = payload.rating
    job["feedback"] = payload.feedback
    persistence.save_job(job_id)
    return {"ok": True, "job": store.get_job(job_id), "mock": RUNTIME.backend == "mock"}


# ------------------------- Content iterations -------------------------

@app.get("/api/projects/{project_id}/iterations", tags=["iterations"])
def api_iterations(project_id: str) -> dict[str, Any]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "iterations": store.list_iterations(project_id),
        "analysis": store.analyze_iterations(project_id),
        "mock": True,
    }


@app.post("/api/projects/{project_id}/iterations", tags=["iterations"])
def api_create_iteration(project_id: str, payload: IterationPayload) -> dict[str, Any]:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    limit = store.video_limit()
    if limit is not None:
        left = max(0, limit - store.SUBSCRIPTION["creditsUsed"])
        if payload.videosToGenerate > left:
            raise HTTPException(status_code=402, detail=f"Only {left} generations are available")
    try:
        iteration, job = store.create_iteration(project_id, payload.videosToGenerate, payload.testParameter)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "iteration": iteration,
        "job": job,
        "redirectTo": f"/app/processing/{job['id']}",
        "mock": True,
    }


# ------------------------- TikTok (Login Kit) and profile -------------------------
# Ключи — только из окружения (.env, он в .gitignore). Мок-подключение разрешено
# только в dev; production без ключей должен завершаться понятным состоянием
# «провайдер не настроен», чтобы не выдавать пользователю фиктивный безлимит.

def _app_url() -> str:
    """Куда вернуть пользователя после OAuth."""
    return RUNTIME.app_url


MOCK_SHARED_OPEN_ID = "mock_open_id_shared"


def _mock_open_id(reuse: bool) -> str:
    """open_id для мок-подключения (когда ключей TikTok нет).

    По умолчанию свой на каждый аккаунт — иначе анти-фрод срабатывал бы на любом втором
    локальном аккаунте, и погонять обычный флоу было бы нельзя. `?reuse=1` (только при
    включённых dev-ручках) даёт ОБЩИЙ open_id — так проверяется сам бан.
    """
    if reuse and DEV_TOOLS:
        return MOCK_SHARED_OPEN_ID
    return f"mock_open_id_{store.current_user_id()}"


def _connect_mock(open_id: str = "mock_open_id") -> None:
    store.connect_tiktok(handle="808max", open_id=open_id, tokens={
        "access_token": "mock_access_token",
        "refresh_token": "mock_refresh_token",
        "expires_in": 86400,
        "scope": tiktok_config.load().scopes,
    })


def _token_record(tokens: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    expires_in = int(tokens.get("expires_in") or 0)
    refresh_expires_in = int(tokens.get("refresh_expires_in") or 0)
    return {
        "accessToken": tokens.get("access_token"),
        "refreshToken": tokens.get("refresh_token"),
        "expiresAt": (now + timedelta(seconds=expires_in)).isoformat() if expires_in else None,
        "refreshExpiresAt": (now + timedelta(seconds=refresh_expires_in)).isoformat() if refresh_expires_in else None,
        "scope": tokens.get("scope"),
        "openId": tokens.get("open_id") or profile.get("open_id") or "",
        "handle": profile.get("display_name") or "",
        "avatarUrl": profile.get("avatar_url"),
    }


def _apply_token_profile(record: dict[str, Any]) -> None:
    store.connect_tiktok(
        handle=str(record.get("handle") or ""),
        open_id=str(record.get("openId") or ""),
        avatar_url=record.get("avatarUrl"),
        tokens={
            "scope": record.get("scope"),
            "expires_in": max(0, int((datetime.fromisoformat(record["expiresAt"]) - datetime.now(timezone.utc)).total_seconds())) if record.get("expiresAt") else 0,
        },
    )


def _refresh_tiktok_tokens(force: bool = False) -> dict[str, Any] | None:
    cfg = tiktok_config.load()
    record = tiktok_token_store.load(store.USER["id"])
    if not cfg.configured or not record:
        return record
    if not force and not tiktok_token_store.needs_refresh(record):
        return record
    refresh = record.get("refreshToken")
    if not refresh:
        return None
    tokens = tiktok_api.refresh_token(cfg, str(refresh))
    # TikTok may rotate the refresh token. If a response omits an unchanged
    # value, retain the encrypted one instead of disconnecting the account.
    tokens["refresh_token"] = tokens.get("refresh_token") or record.get("refreshToken")
    tokens["access_token"] = tokens.get("access_token") or record.get("accessToken")
    if not tokens.get("refresh_expires_in") and record.get("refreshExpiresAt"):
        try:
            tokens["refresh_expires_in"] = max(0, int((datetime.fromisoformat(record["refreshExpiresAt"]) - datetime.now(timezone.utc)).total_seconds()))
        except ValueError:
            pass
    profile = {
        "open_id": record.get("openId"),
        "display_name": record.get("handle"),
        "avatar_url": record.get("avatarUrl"),
    }
    updated = _token_record(tokens, profile)
    tiktok_token_store.save(store.USER["id"], updated)
    _apply_token_profile(updated)
    return updated


def _access_token() -> str:
    record = _refresh_tiktok_tokens()
    token = record.get("accessToken") if record else None
    if not token:
        raise HTTPException(status_code=409, detail="TikTok authorization has expired; reconnect the account")
    return str(token)


@app.on_event("startup")
def _restore_tiktok_and_start_refresh() -> None:
    record = tiktok_token_store.load(store.USER["id"])
    if tiktok_config.load().configured and record:
        _apply_token_profile(record)
    tiktok_token_store.ensure_scheduler(_refresh_tiktok_tokens)
    # поднимаем TG-поллер сразу на старте — чтобы /start ловился без «прогрева» на первом логине
    telegram_bot.ensure_started()


def _finish_tiktok_connect(*, handle: str, open_id: str, mock: bool = False,
                           tokens: dict[str, Any] | None = None,
                           info: dict[str, Any] | None = None) -> RedirectResponse:
    """Единая точка подключения TikTok: сперва анти-фрод, только потом сохранение.

    Проверка стоит ДО записи специально: подключить аккаунт и тут же забанить — значит
    отдать безлимит на время между двумя запросами. Оба пути (реальный OAuth и мок без
    ключей) идут здесь, иначе правило обходилось бы локально запущенным экземпляром.
    """
    user_id = store.current_user_id()
    verdict = fraud_guard.register_connection(open_id, user_id)
    if verdict is not None:
        if verdict.get("banned"):
            analytics.track("tiktok_reuse_blocked", user_id, {"accounts": verdict.get("accounts")})
            return RedirectResponse(f"{_app_url()}/blocked", status_code=302)
        # Реестр недоступен — не подключаем (fail-closed): молча пропустить проверку хуже
        return RedirectResponse(f"{_app_url()}/app/profile?tiktok=guard_error", status_code=302)

    if mock:
        _connect_mock(open_id)
        persistence.flush_user(user_id)
        return RedirectResponse(f"{_app_url()}/app/profile?tiktok=mock", status_code=302)

    store.connect_tiktok(
        handle=handle,
        open_id=open_id,
        tokens=tokens or {},
        avatar_url=(info or {}).get("avatar_url"),
    )
    tiktok_token_store.save(user_id, _token_record(tokens or {}, info or {}))
    analytics.track("tiktok_connected", user_id, {"handle": handle})
    # Колбэк — GET, а сброс в middleware висит на мутирующих методах: без явного вызова
    # подключение не попало бы в БД до следующей правки чего-нибудь другого.
    persistence.flush_user(user_id)
    return RedirectResponse(f"{_app_url()}/app/profile?tiktok=connected", status_code=302)


@app.get("/api/tiktok/auth", tags=["tiktok"])
def api_tiktok_auth(request: Request, reuse: bool = False) -> RedirectResponse:
    """Старт OAuth: уводим на TikTok. state и PKCE-verifier кладём в серверную сессию."""
    cfg = tiktok_config.load()
    if not cfg.configured:
        if RUNTIME.production:
            return RedirectResponse(f"{_app_url()}/app/profile?tiktok=not_configured", status_code=302)
        return _finish_tiktok_connect(handle="808max", open_id=_mock_open_id(reuse), mock=True)

    state = secrets.token_urlsafe(24)
    verifier, challenge = tiktok_api.new_pkce()
    request.session["tiktok_state"] = state
    request.session["tiktok_verifier"] = verifier
    return RedirectResponse(tiktok_api.build_auth_url(cfg, state, challenge), status_code=302)


@app.get("/api/tiktok/callback", tags=["tiktok"])
def api_tiktok_callback(request: Request, code: str | None = None, state: str | None = None,
                        error: str | None = None, reuse: bool = False) -> RedirectResponse:
    """Возврат от TikTok: сверяем state (CSRF), меняем code на токен, тянем профиль."""
    cfg = tiktok_config.load()
    if not cfg.configured:
        if RUNTIME.production:
            return RedirectResponse(f"{_app_url()}/app/profile?tiktok=not_configured", status_code=302)
        return _finish_tiktok_connect(handle="808max", open_id=_mock_open_id(reuse), mock=True)

    if error:
        return RedirectResponse(f"{_app_url()}/app/profile?tiktok=denied", status_code=302)

    saved_state = request.session.pop("tiktok_state", None)
    verifier = request.session.pop("tiktok_verifier", None)
    if not code or not state or not saved_state or state != saved_state or not verifier:
        # чужой/протухший редирект — токен не запрашиваем
        return RedirectResponse(f"{_app_url()}/app/profile?tiktok=error", status_code=302)

    try:
        tokens = tiktok_api.exchange_code(cfg, code, verifier)
        info = tiktok_api.fetch_user_info(tokens["access_token"])
    except Exception:
        return RedirectResponse(f"{_app_url()}/app/profile?tiktok=error", status_code=302)

    return _finish_tiktok_connect(
        handle=info.get("display_name") or "",
        open_id=tokens.get("open_id") or info.get("open_id") or "",
        tokens=tokens,
        info=info,
    )


@app.post("/api/tiktok/post", tags=["tiktok"])
def api_tiktok_post(payload: TiktokPostPayload) -> dict[str, Any]:
    if store.TIKTOK is None:
        raise HTTPException(status_code=409, detail="TikTok is not connected")
    if not payload.rights:
        raise HTTPException(status_code=422, detail="Content rights must be confirmed")
    found = store.find_video(payload.videoId)
    if not found:
        raise HTTPException(status_code=404, detail="Generated video not found")
    job, video = found
    if job.get("projectId") != payload.projectId or video.get("status") != "COMPLETED":
        raise HTTPException(status_code=409, detail="Video is not ready for posting")
    video_url = video.get("downloadUrl")
    if not video_url:
        raise HTTPException(status_code=409, detail="Rendered video file is unavailable")

    privacy = {
        "all": "PUBLIC_TO_EVERYONE",
        "followers": "FOLLOWER_OF_CREATOR",
        "friends": "MUTUAL_FOLLOW_FRIENDS",
        "self": "SELF_ONLY",
    }.get(payload.privacy)
    if not privacy:
        raise HTTPException(status_code=422, detail="Unsupported TikTok privacy level")
    cfg = tiktok_config.load()
    if not cfg.configured and RUNTIME.production:
        raise HTTPException(status_code=503, detail={"code": "tiktok_not_configured"})
    try:
        if cfg.configured:
            token = _access_token()
            creator = tiktok_api.query_creator_info(token)
        else:
            token = ""
            creator = {
                "privacy_level_options": [
                    "PUBLIC_TO_EVERYONE",
                    "MUTUAL_FOLLOW_FRIENDS",
                    "SELF_ONLY",
                ],
                "comment_disabled": False,
                "duet_disabled": False,
                "stitch_disabled": False,
            }
        tiktok_api.validate_video_post_settings(
            creator,
            privacy_level=privacy,
            comments=payload.comments,
            duet=payload.duet,
            stitch=payload.stitch,
            brand_content=payload.brandContent,
        )
        post_info = tiktok_api.build_video_post_info(
            title=payload.caption,
            privacy_level=privacy,
            comments=payload.comments,
            duet=payload.duet,
            stitch=payload.stitch,
            cover_timestamp_ms=payload.coverTimestampMs,
            brand_content=payload.brandContent,
            brand_organic=payload.brandOrganic,
        )
        # Do not log captions or media URLs. These booleans are enough to audit
        # the Content Posting payload, including the deliberate absence of AIGC.
        logger.info(
            "tiktok_post_settings privacy=%s disable_comment=%s disable_duet=%s "
            "disable_stitch=%s brand_content=%s brand_organic=%s is_aigc_sent=%s",
            privacy,
            post_info["disable_comment"],
            post_info["disable_duet"],
            post_info["disable_stitch"],
            post_info["brand_content_toggle"],
            post_info["brand_organic_toggle"],
            "is_aigc" in post_info,
        )
        if not cfg.configured:
            publish_id = f"mock_tt_{uuid4().hex[:8]}"
            video.update({"tiktokPublishId": publish_id, "tiktokStatus": "PUBLISH_COMPLETE", "postedAt": datetime.now(timezone.utc).isoformat()})
            analytics.track("video_posted", store.current_user_id(), {"videoId": payload.videoId, "projectId": payload.projectId, "mock": True})
            return {"ok": True, "status": "PUBLISH_COMPLETE", "publishId": publish_id, "mock": True}

        if cfg.upload_source == "PULL_FROM_URL":
            result = tiktok_api.init_direct_post_pull(token, post_info, str(video_url))
        elif str(video_url).startswith("/static/"):
            local_path = STATIC_DIR / str(video_url).removeprefix("/static/")
            result = tiktok_api.init_direct_post_file(token, post_info, local_path)
        elif RUNTIME.production:
            with tempfile.TemporaryDirectory(prefix="blast-tiktok-") as temp_dir:
                local_path = Path(temp_dir) / f"{payload.videoId}.mp4"
                try:
                    _production_backend().download_video(str(video_url), local_path)
                except Exception as exc:
                    raise _production_error(exc) from exc
                result = tiktok_api.init_direct_post_file(token, post_info, local_path)
        else:
            raise HTTPException(
                status_code=422,
                detail="FILE_UPLOAD requires a local rendered MP4 outside production",
            )
    except tiktok_api.TikTokPostValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except tiktok_api.TikTokApiError as exc:
        raise HTTPException(status_code=exc.status or 502, detail={"code": exc.code, "message": str(exc)}) from exc
    publish_id = result.get("publish_id")
    video.update({"tiktokPublishId": publish_id, "tiktokStatus": "PROCESSING_UPLOAD"})
    return {"ok": True, "status": "PROCESSING_UPLOAD", "publishId": publish_id, "mock": False}


@app.get("/api/tiktok/post/{publish_id}", tags=["tiktok"])
def api_tiktok_post_status(publish_id: str) -> dict[str, Any]:
    if not tiktok_config.load().configured:
        if RUNTIME.production:
            raise HTTPException(status_code=503, detail={"code": "tiktok_not_configured"})
        return {"publishId": publish_id, "status": "PUBLISH_COMPLETE", "mock": True}
    try:
        result = tiktok_api.fetch_publish_status(_access_token(), publish_id)
    except tiktok_api.TikTokApiError as exc:
        raise HTTPException(status_code=exc.status or 502, detail={"code": exc.code, "message": str(exc)}) from exc
    status = result.get("status")
    if status == "PUBLISH_COMPLETE":
        for job in store.JOBS.values():
            if job.get("userId") != store.current_user_id():
                continue
            video = next((item for item in job.get("videos", []) if item.get("tiktokPublishId") == publish_id), None)
            if video:
                video.update({
                    "tiktokStatus": status,
                    "postedAt": datetime.now(timezone.utc).isoformat(),
                    "tiktokPostIds": result.get("publicaly_available_post_id") or [],
                })
                persistence.save_job(str(job["id"]))
                break
    return {"publishId": publish_id, **result, "mock": False}


@app.get("/api/tiktok/creator-info", tags=["tiktok"])
def api_tiktok_creator_info() -> dict[str, Any]:
    if store.TIKTOK is None:
        raise HTTPException(status_code=409, detail="TikTok is not connected")
    if not tiktok_config.load().configured:
        if RUNTIME.production:
            raise HTTPException(status_code=503, detail={"code": "tiktok_not_configured"})
        return {
            "creator_nickname": store.TIKTOK.get("handle"),
            "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
            "duet_disabled": False,
            "comment_disabled": False,
            "stitch_disabled": False,
            "mock": True,
        }
    try:
        return {**tiktok_api.query_creator_info(_access_token()), "mock": False}
    except tiktok_api.TikTokApiError as exc:
        raise HTTPException(status_code=exc.status or 502, detail={"code": exc.code, "message": str(exc)}) from exc


@app.get("/api/tiktok/videos", tags=["tiktok"])
def api_tiktok_videos(days: int = 30) -> dict[str, Any]:
    if store.TIKTOK is None:
        raise HTTPException(status_code=409, detail="TikTok is not connected")
    if not tiktok_config.load().configured:
        if RUNTIME.production:
            raise HTTPException(status_code=503, detail={"code": "tiktok_not_configured"})
        posted = [video for job in store.JOBS.values() for video in job.get("videos", []) if video.get("tiktokStatus") == "PUBLISH_COMPLETE"]
        videos = [{
            "id": video["id"], "create_time": int(datetime.now(timezone.utc).timestamp()),
            "view_count": 0, "like_count": 0, "comment_count": 0, "share_count": 0,
            "cover_image_url": video.get("thumbnailUrl"), "share_url": video.get("downloadUrl"),
        } for video in posted]
        return {"videos": videos, "hasMore": False, "retentionAvailable": False, "mock": True}
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    videos: list[dict[str, Any]] = []
    cursor: int | None = None
    has_more = True
    try:
        while has_more and len(videos) < 100:
            page = tiktok_api.list_videos(_access_token(), cursor=cursor, max_count=20)
            batch = page.get("videos") or []
            for item in batch:
                created = datetime.fromtimestamp(int(item.get("create_time") or 0), tz=timezone.utc)
                if created >= cutoff:
                    videos.append(item)
            has_more = bool(page.get("has_more")) and bool(batch)
            cursor = page.get("cursor")
            if batch and datetime.fromtimestamp(int(batch[-1].get("create_time") or 0), tz=timezone.utc) < cutoff:
                has_more = False
    except tiktok_api.TikTokApiError as exc:
        raise HTTPException(status_code=exc.status or 502, detail={"code": exc.code, "message": str(exc)}) from exc
    by_id = {str(item.get("id")): item for item in videos if item.get("id")}
    current_user = store.current_user_id()
    for job in store.JOBS.values():
        if job.get("userId") != current_user:
            continue
        changed = False
        for video in job.get("videos", []):
            matched = next(
                (by_id.get(str(post_id)) for post_id in video.get("tiktokPostIds", []) if by_id.get(str(post_id))),
                None,
            )
            if not matched:
                continue
            video["metrics"] = {
                "view_count": int(matched.get("view_count") or 0),
                "like_count": int(matched.get("like_count") or 0),
                "comment_count": int(matched.get("comment_count") or 0),
                "share_count": int(matched.get("share_count") or 0),
            }
            if matched.get("cover_image_url"):
                video["thumbnailUrl"] = matched["cover_image_url"]
            video["tiktokShareUrl"] = matched.get("share_url")
            video["metricsSyncedAt"] = datetime.now(timezone.utc).isoformat()
            changed = True
        if changed:
            persistence.save_job(str(job["id"]))
    return {"videos": videos, "hasMore": has_more, "retentionAvailable": False, "mock": False}


@app.delete("/api/tiktok/disconnect", tags=["tiktok"])
def api_tiktok_disconnect() -> dict[str, Any]:
    # Отключение возвращает лимит триала (5 роликов) — см. mock_store.video_limit
    store.disconnect_tiktok()
    tiktok_token_store.delete(store.USER["id"])
    return {"ok": True}


@app.get("/api/tiktok/status", tags=["tiktok"])
def api_tiktok_status() -> dict[str, Any]:
    """Готовы ли ключи. Фронту нужно, чтобы честно сказать «идёт мок-подключение»."""
    cfg = tiktok_config.load()
    return {
        "configured": cfg.configured,
        "scopes": cfg.scopes,
        "redirectUri": cfg.redirect_uri,
        "uploadSource": cfg.upload_source,
    }


@app.patch("/api/profile", tags=["profile"])
def api_profile(payload: ProfilePayload) -> dict[str, Any]:
    data = {k: v.strip() if isinstance(v, str) else v for k, v in payload.model_dump(exclude_none=True).items()}
    user = store.ws().user
    user.update(data)
    # ФИО обязательны: очистить их через профиль нельзя
    if not (user.get("name") or "").strip():
        raise HTTPException(status_code=422, detail="Имя обязательно")
    return {"user": user, "mock": RUNTIME.backend == "mock"}


@app.delete("/api/profile", tags=["profile"])
def api_delete_account(request: Request, payload: DeleteAccountPayload) -> dict[str, Any]:
    if payload.confirmation != "DELETE":
        raise HTTPException(status_code=422, detail="confirmation must equal DELETE")
    user_id = str(request.session.get("user_id") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    if RUNTIME.backend == "production":
        try:
            _production_backend().delete_user_objects(user_id)
        except Exception as exc:
            raise _production_error(exc) from exc
    from . import upload_store
    if RUNTIME.backend != "production":
        for asset in upload_store.assets(user_id):
            (SOURCE_DIR / Path(asset["s3Key"]).name).unlink(missing_ok=True)
    upload_store.remove_account(user_id)
    tiktok_token_store.delete(user_id)
    deleted = persistence.delete_account(user_id)
    request.session.clear()
    return {"ok": True, "deleted": deleted}


@app.post("/api/profile/avatar", tags=["profile"])
async def api_avatar(file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read()
    # Аватар видят другие люди — принимаем только настоящий PNG/JPEG, а не то,
    # что клиент назвал картинкой.
    security.check_image(content, max_mb=8)
    ext = security.safe_extension(file.filename, {".png", ".jpg", ".jpeg"}, ".jpg")
    safe_name = f"avatar_{uuid4().hex}{ext}"
    if RUNTIME.backend == "production":
        try:
            uploaded = await run_in_threadpool(
                _production_backend().upload_user_image,
                content=content,
                user_id=store.current_user_id(),
                filename=safe_name,
                content_type=file.content_type,
                kind="avatars",
            )
        except Exception as exc:
            raise _production_error(exc) from exc
        store.USER["avatarUrl"] = uploaded["playback_url"]
    else:
        target = STATIC_DIR / "uploads" / safe_name
        target.write_bytes(content)
        store.USER["avatarUrl"] = f"/static/uploads/{safe_name}"
    return {"avatarUrl": store.USER["avatarUrl"], "mock": RUNTIME.backend == "mock"}


@app.get("/api/videos/{video_id}/frames", tags=["videos"])
def api_video_frames(video_id: str, count: int = 8) -> dict[str, Any]:
    """Раскадровка ролика для пикера обложки в модалке выкладки."""
    if not 2 <= count <= 32:
        raise HTTPException(status_code=422, detail="count должен быть от 2 до 32")
    data = store.video_frames(video_id, count)
    if not data:
        raise HTTPException(status_code=404, detail="Video not found")
    data["mock"] = True
    return data


@app.post("/api/projects/{project_id}/activate", tags=["projects"])
def api_activate_project(project_id: str) -> dict[str, Any]:
    """Сделать проект текущим — до этого переключить его было нечем."""
    project = store.set_current_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project": project, "mock": True}


@app.post("/api/projects/{project_id}/cover", tags=["projects"])
async def api_project_cover(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    """Обложка проекта: грузится отдельным запросом после создания (она опциональна)."""
    project = next((p for p in store.PROJECTS if p["id"] == project_id), None)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    content = await file.read()
    # Проверка по содержимому, а не по расширению: раньше достаточно было переименовать файл
    security.check_image(content, max_mb=8)
    ext = security.safe_extension(file.filename, {".png", ".jpg", ".jpeg"}, ".jpg")
    safe_name = f"cover_{uuid4().hex}{ext}"
    if RUNTIME.backend == "production":
        try:
            uploaded = await run_in_threadpool(
                _production_backend().upload_user_image,
                content=content,
                user_id=store.current_user_id(),
                filename=safe_name,
                content_type=file.content_type,
                kind="covers",
            )
        except Exception as exc:
            raise _production_error(exc) from exc
        project["coverUrl"] = uploaded["playback_url"]
    else:
        (STATIC_DIR / "uploads" / safe_name).write_bytes(content)
        project["coverUrl"] = f"/static/uploads/{safe_name}"
    project["coverChoice"] = "upload"
    return {"coverUrl": project["coverUrl"], "mock": RUNTIME.backend == "mock"}


@app.post("/api/dev/login", tags=["system"])
def api_dev_login(request: Request, user: str = store.DEMO_USER_ID) -> dict[str, Any]:
    """DEV-ручка: войти без Telegram (гард авторизации включён по умолчанию).

    `?user=<id>` открывает произвольный аккаунт — так проверяется изоляция данных.
    """
    request.session["user_id"] = user
    store.use_user(user)
    space = store.workspace(user)
    return {"ok": True, "userId": user, "projects": len(space.projects)}


@app.post("/api/dev/ban", tags=["system"])
def api_dev_ban(request: Request, on: bool = True, reason: str = fraud_guard.BAN_TIKTOK_REUSE) -> dict[str, Any]:
    """DEV-ручка: включить/снять бан текущему аккаунту, чтобы посмотреть экран блокировки.

    Реальный бан ставится только анти-фродом на подключении TikTok, а тот пишет связку в
    реестр использованных аккаунтов НАВСЕГДА. Проверять экран настоящим переиспользованием
    значило бы засорять дев-базу неудаляемыми доказательствами — поэтому флаг ставится здесь
    напрямую. `?on=false` снимает.
    """
    user_id = request.session.get("user_id") or store.current_user_id()
    stub_key = f"dev:{user_id}"
    if on:
        if auth_store.user_by_id(user_id) is None:
            # Демо-аккаунт (user_1) живёт только воркспейсом, записи в реестре у него нет —
            # а флаг бана хранится именно там. Заводим минимальную запись и помечаем её,
            # чтобы снятие бана убрало её за собой и не оставило мусор в реестре.
            auth_store.USERS[stub_key] = {"id": user_id, "email": "", "name": "dev", "surname": "", "devStub": True}
        fraud_guard.ban_users([user_id], reason)
    else:
        fraud_guard.unban_users([user_id])
        if (auth_store.USERS.get(stub_key) or {}).get("devStub"):
            persistence.delete_user(stub_key)
    return {"ok": True, "userId": user_id, "ban": fraud_guard.ban_status(user_id)}


# ------------------------- Analytics (админка) -------------------------

# Кто видит админку. Пусто → в деве доступна всем залогиненным, в проде — никому.
ADMIN_USER_IDS = {uid.strip() for uid in os.getenv("BLAST_ADMIN_USER_IDS", "").split(",") if uid.strip()}


def _require_admin() -> None:
    if ADMIN_USER_IDS and store.current_user_id() not in ADMIN_USER_IDS:
        raise HTTPException(status_code=403, detail="Недостаточно прав")


@app.get("/api/admin/analytics", tags=["admin"])
def api_admin_analytics(days: int = 30, weeks: int = 4) -> dict[str, Any]:
    """Сводка, воронка и удержание для раздела аналитики."""
    _require_admin()
    if not 1 <= days <= 365:
        raise HTTPException(status_code=422, detail="days: 1..365")
    return {
        "summary": analytics.summary(days),
        "funnel": analytics.funnel(days),
        "retention": analytics.retention(weeks),
        # разрез по выкладке: где человек остановился и доходит ли контент до площадки
        "delivery": analytics.delivery_summary(days),
        # прохождение: отвал на ожидании, время на «Пуле», возвраты назад
        "flow": analytics.flow_metrics(days),
        "journeys": analytics.user_journeys(days)[:100],
        "recent": analytics.recent(30),
        "isAdmin": True,
    }


@app.post("/api/analytics/track", tags=["analytics"])
def api_track(payload: TrackPayload) -> dict[str, Any]:
    """Событие с фронта (клиентские шаги воронки, которых не видно на бэке)."""
    event = analytics.track(payload.name, store.current_user_id(), payload.props)
    return {"ok": True, "id": event["id"]}


@app.post("/api/dev/billing/{state}", tags=["system"])
def api_dev_billing(state: str, monthsAgo: int = 0) -> dict[str, Any]:
    """DEV-ручка: past_due | active | canceled — проверка состояний оплаты в UI.

    `monthsAgo` отматывает дату старта подписки назад — так проверяется шкала бонусных
    месяцев, не дожидаясь реального месяца.
    """
    if monthsAgo:
        sub = store.SUBSCRIPTION
        started = datetime.fromisoformat(sub["startedAt"]) - timedelta(days=31 * monthsAgo)
        sub["startedAt"] = started.isoformat()
    if state == "past_due":
        sub = store.mark_payment_failed()
    elif state == "active":
        sub = store.mark_payment_ok()
    elif state == "canceled":
        sub = store.cancel_subscription(at_period_end=True)
    else:
        raise HTTPException(status_code=422, detail="state: past_due | active | canceled")
    return {"ok": True, "subscription": sub}


@app.post("/api/dev/mark-posted/{video_id}", tags=["system"])
def api_dev_mark_posted(video_id: str) -> dict[str, Any]:
    """DEV-ручка: пометить ролик опубликованным — для проверки состояний батча без TikTok."""
    found = store.find_video(video_id)
    if not found:
        raise HTTPException(status_code=404, detail="Video not found")
    _, video = found
    video.update({
        "tiktokStatus": "PUBLISH_COMPLETE",
        "postedAt": datetime.now(timezone.utc).isoformat(),
        "tiktokPublishId": f"dev_{uuid4().hex[:8]}",
    })
    analytics.track("video_posted", store.current_user_id(), {"videoId": video_id, "dev": True})
    return {"ok": True, "videoId": video_id}


@app.post("/api/dev/fail-job/{job_id}", tags=["system"])
def api_dev_fail_job(job_id: str) -> dict[str, Any]:
    """DEV-ручка: уронить джоб, чтобы проверить экран «Генерация не удалась» и возврат кредитов."""
    job = store.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    lost = sum(1 for v in job["videos"] if v["status"] != "COMPLETED")
    job["status"] = "FAILED"
    for video in job["videos"]:
        if video["status"] != "COMPLETED":
            video["status"] = "FAILED"
            video["stage"] = "failed"
    store.SUBSCRIPTION["creditsUsed"] = max(0, store.SUBSCRIPTION["creditsUsed"] - lost)
    return {"ok": True, "job": job_id, "refunded": lost}


@app.post("/api/dev/reset", tags=["system"])
def api_dev_reset(empty: bool = False) -> dict[str, Any]:
    """DEV-ручка для аудита состояний: сбрасывает мок-данные.

    `empty=1` — эмуляция «нулевого» аккаунта: без проектов, треков и исходников.
    Восстановить сид можно рестартом бэка (он засевается заново при импорте).
    """
    space = store.ws()
    for job_id in [jid for jid, job in store.JOBS.items() if job.get("userId") == space.user["id"]]:
        store.JOBS.pop(job_id, None)
    store.ITERATIONS.clear()
    store.reset_to_trial()
    space.subscription["creditsUsed"] = 0
    space.subscription["tracksUsed"] = 0
    space.tiktok = None
    if empty:
        space.projects.clear()
        space.saved_tracks.clear()
        space.user_sources.clear()
        space.active_project_id = None
    return {"ok": True, "empty": empty, "projects": len(space.projects), "tracks": len(space.saved_tracks)}


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, Any]:
    if RUNTIME.backend == "production":
        try:
            await run_in_threadpool(_production_backend().healthcheck)
            await _billing_backend().healthcheck()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "dependency_unhealthy", "message": str(exc)},
            ) from exc
    return {"ok": True, "mode": RUNTIME.mode, "backend": RUNTIME.backend, "mock": RUNTIME.backend == "mock"}
