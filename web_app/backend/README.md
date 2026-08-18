# Blast Web API

FastAPI API для SPA Blast. Vite SPA отдаёт nginx; старый параллельный Jinja-сайт удалён.
Режим запуска выбирается только явно: `MODE=dev` + `BLAST_BACKEND_MODE=mock` для
локальной разработки или `MODE=prod` + `BLAST_BACKEND_MODE=production` для прода.

Production env, deploy workflow и приёмочный smoke описаны в
[`../../PRE-DEPLOY.md`](../../PRE-DEPLOY.md). Пример полного env-контракта:
[`./.env.production.example`](./.env.production.example).

## Быстрый запуск

```bash
cd web_app/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export MODE=dev
export BLAST_BACKEND_MODE=mock
export APP_URL=http://localhost:5173
export BLAST_CORS_ORIGINS=http://localhost:5173
export BLAST_SESSION_SECRET=local-development-only
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Открыть:

- `http://localhost:8000/register`
- `http://localhost:8000/login`
- `http://localhost:8000/app`
- `http://localhost:8000/app/generate`
- `http://localhost:8000/docs`

## Персистентность

Состояние приложения переживает рестарт: воркспейсы (профиль, подписка, проекты, треки),
батчи генерации, итерации и поток аналитики лежат в БД. Схема — `backend/db/migrations`,
миграции применяются автоматически при старте.

- **По умолчанию — SQLite**, файл `backend/data/blast.db`. Ничего ставить не нужно.
- **Postgres** включается переменной `DATABASE_URL` (нужен `psycopg[binary]`).
  Тот же URL включает и PG-режим рендер-очереди (`render_store.PgRenderStore`).

Реестр аккаунтов раньше жил в `backend/data/users.json`; при первом старте он
разово переносится в таблицу `app_users`, файл остаётся как бэкап.

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `DATABASE_URL` | — | Postgres вместо SQLite |
| `BLAST_DB_PATH` | `backend/data/blast.db` | путь к файлу SQLite |
| `BLAST_PERSIST` | `1` | `0` — работать целиком в памяти (тесты) |

## Вход

Два способа, оба passwordless:

- **Telegram** — личность = `chat_id` из `/start <token>`. Токены подтверждения лежат в БД
  (`auth_tokens`), а не в памяти: иначе бот и веб в разных процессах не видят один токен,
  и подтверждение приходится повторять по нескольку раз.
- **Google** (OAuth 2.0, code flow) — ради западной аудитории. `client_secret` уходит только
  на сервер Google, `state` лежит в сессии и сверяется на возврате. Скоупы `openid email
  profile` — не чувствительные, ревью приложения в Google не требуется.

Кнопка Google появляется на экране входа, только если заданы ключи (`/api/auth/providers`).

| Переменная | Зачем |
|---|---|
| `GOOGLE_CLIENT_ID` | из Google Cloud Console → Credentials → OAuth client (Web application) |
| `GOOGLE_CLIENT_SECRET` | оттуда же |
| `GOOGLE_REDIRECT_URI` | должен совпадать с консолью буква в букву; Google требует https везде, кроме `http://localhost` |
| `APP_URL` | куда вернуть пользователя после входа |
| `TIKTOK_UPLOAD_SOURCE` | `FILE_UPLOAD` в production; `PULL_FROM_URL` допустим только после верификации собственного media-домена в TikTok |
| `TELEGRAM_BOT_TOKEN` | без него бот-верификация выключена, работает дев-фолбэк |
| `BLAST_GEO_HEADER` | заголовок прокси со страной, по умолчанию `CF-IPCountry` |
| `BLAST_GOOGLE_BLOCKED_COUNTRIES` | где не предлагать Google, по умолчанию `RU` |

**Что остаётся сделать в консоли Google после деплоя:** заполнить consent screen (нужны
публичные страницы оферты и политики), добавить redirect URI боевого домена и перевести
приложение из Testing в Production — иначе войти смогут только тестовые аккаунты.

**Связывание аккаунтов.** В профиле есть кнопка «подключить Google»: почта пишется полем
`googleEmail` на существующий аккаунт, и вход через Google по ней ведёт в ТОТ ЖЕ аккаунт.
Второй ключ в реестре на того же юзера не заводим — на `user_id` стоит UNIQUE. Отвязать
последний способ входа нельзя (409 `last_provider`).

**Гео-ограничение.** Из России Google не предлагаем — риск штрафов. Страна берётся из
заголовка прокси; если заголовка нет, страна неизвестна и подключение разрешено. Это
осознанно мягкая проверка: без VPN сервис из России и так недоступен, а с VPN адрес будет
не российским. Для жёсткой блокировки нужен прокси, который заголовок проставляет.

Если пользователь сначала вошёл разными способами, это две разные личности. Чтобы второй
способ вёл в тот же аккаунт, его нужно привязать в профиле до отдельной регистрации.

## Безопасность

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `BLAST_SESSION_SECRET` | случайный | ключ подписи сессии; в проде задать обязательно |
| `BLAST_COOKIE_SECURE` | `0` | `1` — cookie только по HTTPS (прод) |
| `BLAST_COOKIE_SAMESITE` | `lax` | `strict` ломает возврат с OAuth TikTok — см. ниже |
| `BLAST_CORS_ORIGINS` | `http://localhost:5173,…` | домены фронта через запятую |
| `BLAST_CSRF` | `1` | `0` — выключить проверку CSRF |
| `BLAST_RATE_LIMIT` | `1` | `0` — выключить ограничение частоты |
| `BLAST_RATE_AUTH` | `10` | запросов в минуту на IP для `/api/auth/*` |
| `BLAST_RATE_UPLOAD` | `20` | запросов в минуту на IP для загрузок |
| `REDIS_URL` | — | общий счётчик лимитов, когда воркеров больше одного |
| `BLAST_REQUIRE_AUTH` | `1` | `0` — открыть моки без входа (только локально) |
| `BLAST_DEV_TOOLS` | `1` | `0` — `/api/dev/*` отвечают 404 (обязательно в проде) |

**CSRF.** Схема double-submit: сервер кладёт токен в НЕ-HttpOnly cookie `blast_csrf`,
фронт возвращает его заголовком `X-CSRF-Token`. Небезопасные методы на `/api/*` без
совпадения дают `403 {code:"csrf_failed"}` — фронт по этому коду обновляет токен и
повторяет запрос один раз. Исключения: возврат OAuth TikTok и вебхук платёжки
(сервер-сервер, у него своя подпись).

**Почему SameSite не `strict`.** При `strict` браузер не пришлёт cookie сессии на
возврате с домена TikTok после OAuth, и подключение аккаунта перестанет работать.
От межсайтовых запросов защищает CSRF-токен. Значение вынесено в переменную:
если OAuth-возврат когда-нибудь переедет на собственный обмен, `strict` можно включить.

**Загрузки** проверяются по содержимому (первые байты), а не по `content_type` от
клиента: под видом mp3 или png раньше можно было залить что угодно. Расширение файла
берётся из белого списка, имя санируется.

**Счётчики частоты** по умолчанию живут в памяти процесса — этого хватает одному инстансу.
При нескольких воркерах задайте `REDIS_URL` (например `redis://localhost:6379/0`): счёт
уедет в общее хранилище, иначе каждый воркер считает свой лимит и суммарный оказывается
кратно больше заявленного. Окно в Redis фиксированное (`INCR` + `EXPIRE` на интервал),
в памяти — скользящее; разница для защиты от перебора несущественна. Если библиотеки
`redis` нет или сервер недоступен, production healthcheck падает: общий лимит нельзя
незаметно заменить отдельными счётчиками процессов. In-memory limiter разрешён только в dev.

## Анти-фрод: один аккаунт TikTok — один бесплатный лимит

Подключение TikTok открывает безлимит в рамках трека, поэтому аккаунт TikTok — вторая
ступень проверки «человек настоящий». Модуль `app/fraud_guard.py`:

- каждое подключение пишется в `tiktok_account_usage` (миграция `004_tiktok_guard.*`) —
  только `open_id` и `user_id`, без содержимого профиля;
- если подключаемый аккаунт TikTok уже использовался ДРУГИМ аккаунтом сервиса, банится
  всё «кольцо» — замыкание по графу «юзер ↔ open_id», то есть все аккаунты этого человека;
- история использования НЕ удаляется вместе с аккаунтом: иначе правило обходилось бы
  удалением своего аккаунта перед повторной регистрацией;
- проверка стоит ДО сохранения подключения и работает fail-closed: недоступен реестр —
  подключение не состоится (`?tiktok=guard_error`), а не «пропустим на всякий случай».

Забаненному аккаунту открыт только блок `/api/auth/*`: узнать причину
(`GET /api/auth/ban-status`), выйти и войти под другой личностью. Всё остальное — `403`
с `code: "account_banned"`, по нему фронт уводит на `/blocked`. Сессия при бане намеренно
не рвётся: без неё экран блокировки не смог бы показать причину.

Дев-проверка экрана: `POST /api/dev/ban` ставит флаг текущему аккаунту, `?on=false` снимает
(только при `BLAST_DEV_TOOLS=1`). Реальное переиспользование для проверки гонять не нужно —
оно оставит в реестре неудаляемую запись.

## Юридические страницы

`/legal/policy` и `/legal/offer` — настоящие публичные страницы (тексты в
`frontend/src/data/legal-docs.ts`, обе локали). Их URL прикрепляются в consent screen Google
и в кабинет разработчика TikTok, поэтому они обязаны открываться без входа.

Реквизиты, платёжный партнёр и условия оплаты синхронизированы с лендингом
(`srv_blast/landing/{contacts,offer,privacy}.html`) и зашиты в `LEGAL_ENTITY` /
`PAYMENT_PARTNER`: ИП Чернов Никита Романович, ИНН 623013205426, ОГРНИП 324620000005644,
приём платежей — АО «Т-Банк». Расхождение между лендингом и приложением = отказ модерации,
поэтому значения одни и те же. Если реквизиты сменятся, их можно переопределить через
`frontend/.env` (`VITE_LEGAL_ENTITY`, `_INN`, `_OGRNIP`, `_ADDRESS`, `_EMAIL`, `_PHONE`) —
код при этом не трогается; при пустом значении страница честно скажет «[не заполнено]».

Состав тарифов в оферте описывает ТЕКУЩИЙ веб-продукт (Blast / Glow / Impulse со страницы
тарифов), а не старые пакеты Telegram-бота с лендинга. При изменении цен или состава —
править раздел 6 оферты вместе со страницей тарифов и `LEGAL_UPDATED`.

## Что реализовано

### Страницы

- `/register` — регистрация с Telegram verification modal.
- `/login` — passwordless-вход через Telegram или Google.
- `/app` — dashboard с hero, проектами и блоком статистики.
- `/app/projects` — список проектов и modal создания проекта.
- `/app/projects/{id}` — детали проекта, прогресс, контент-план и оценка батча.
- `/app/profile` — профиль, OAuth-связи, TikTok, подписка и удаление аккаунта.
- `/app/pricing` — 4 тарифа с обязательным чекбоксом оферты перед оплатой.
- `/app/stats` — TikTok Display API метрики и разбор итераций контента.
- `/app/generate` — 5-шаговый wizard: трек → фон → хук → титры → финал.
- `/app/processing/{jobId}` — polling прогресса генерации и inline rating card.
- `/not-found`, `/error` — системные страницы.

### Основные ручки

Auth:

- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/tg-verify`
- `GET /api/me`

Wizard:

- `POST /api/wizard/upload-track`
- `GET /api/wizard/previous-track`
- `GET /api/wizard/drops`
- `GET /api/wizard/vibes`
- `GET /api/wizard/photos`
- `GET /api/wizard/subtitle-styles`
- `GET /api/wizard/session`
- `POST /api/wizard/session`
- `POST /api/wizard/submit`

Preview:

- `GET /api/preview/composite`

Jobs:

- `GET /api/jobs/active`
- `GET /api/jobs/{id}`
- `POST /api/jobs/{id}/rate`

Projects:

- `GET /api/projects`
- `POST /api/projects`
- `GET /api/projects/{id}`

Payments:

- `POST /api/payments/create-order`
- `POST /api/payments/webhook`
- `POST /api/payments/cancel-sub`

TikTok:

- `GET /api/tiktok/auth`
- `GET /api/tiktok/callback`
- `POST /api/tiktok/post`
- `DELETE /api/tiktok/disconnect`

Profile:

- `PATCH /api/profile`
- `POST /api/profile/avatar`

## Ассеты

Оригинальный загруженный RAR сохранён в:

```text
app/static/source_assets/blast_assets_original.rar
```

В текущем окружении не было доступного RAR v5 распаковщика, поэтому для запуска прототипа добавлены SVG-фолбэки с теми же именами:

```text
app/static/assets/logo-main.svg
app/static/assets/logo-wordmark.svg
app/static/assets/logo-glow.svg
app/static/assets/icon-note.svg
app/static/assets/nav-generate.svg
app/static/assets/nav-projects.svg
app/static/assets/nav-stats.svg
```

Nav SVG сделаны через `currentColor` и используются через CSS mask, чтобы активное состояние сайдбара работало от цвета.

## Проверка

```bash
python -m compileall app
python - <<'PY'
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
assert c.get('/healthz').json()['ok'] is True
assert c.get('/app').status_code == 200
assert c.get('/api/projects').json()['mock'] is True
print('OK')
PY
```

## Ограничения мока

- Данные хранятся в памяти и сбрасываются при перезапуске.
- Реальная авторизация, PostgreSQL, подпись TBank webhook, TikTok OAuth и S3 upload не подключены.
- Видео и Remotion preview возвращают mock URL, а UI показывает визуальные placeholders.
- Лендинг `blast808.com` не реализован и не меняется; `/` редиректит в `/app` только для удобства локального просмотра.
