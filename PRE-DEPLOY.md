# Blast Web: production readiness

Актуально на 18 августа 2026. Канонический домен приложения:
`https://app.blast808.com`.

## Статус

Код production-контура готов к первому деплою, но переключать домен с preview на
production пока нельзя: в GitHub нет production env, а у оператора ещё нет полного
набора Google/TikTok/Telegram credentials и реальных web-preview объектов.

Деплой устроен fail-closed: при неполной конфигурации, недоступном Postgres/Redis,
orchestrator/S3, неверном OAuth redirect URI или отсутствующем preview-файле API не
запустится. На mock автоматически не переключается.

## Реализовано

- Явные режимы: `MODE=prod` требует `BLAST_BACKEND_MODE=production`.
- SPA отдаёт nginx, старый Jinja-сайт и дубли его ассетов удалены.
- FastAPI наружу не публикуется; nginx проксирует внутренний API через loopback frontend.
- Production startup проверяет orchestrator, Timeweb S3, web Postgres, общую credits DB,
  Redis, Telegram bot и хранилище TikTok-токенов.
- Сессии, CSRF, secure cookie, CORS, rate limit и `/api/dev/*` валидируются fail-closed.
- Проекты, визард, аккаунты и аналитика сохраняются в Postgres; ошибки записи не
  маскируются продолжением работы в памяти.
- Трек, изображения профиля и F1-аудио загружаются в Timeweb S3.
- Генерация уходит в существующий orchestrator. Вариации одного батча ставятся
  последовательно и переиспользуют Stage 1 через `reuse_text_job_id`.
- Local CTC получает только точный фрагмент и полное окно; неявного fallback на Gemini нет.
- Кредиты и лимиты треков общие с public-ботом. Резерв и возврат по web job идемпотентны.
- Т-Банк использует существующий проверяемый webhook public-бота. Платный тариф не
  активируется до подтверждённой оплаты; для Blast требуется отдельное согласие на рекуррент.
- Ссылки скачивания перевыпускаются с `Content-Disposition: attachment`.
- TikTok OAuth, публикация и Display API sync заполняют реальные метрики роликов.
- Google OAuth, связывание/отвязывание провайдера, TikTok disconnect и удаление аккаунта
  доступны из профиля.
- Визард восстанавливает server-side draft и предыдущий трек.
- В проекте есть оценка последнего завершённого батча.
- Dashboard и `/app/stats` используют один project analysis; периодные цифры берутся из
  TikTok Display API.
- Персональный менеджер имеет рабочий support fallback вместо вымышленного сотрудника.
- Production deploy сначала собирает и проверяет новый stack; preview выключается только
  перед переключением. При ошибке preview автоматически поднимается обратно.

## Обязательные данные

Создать `web_app/backend/.env.production` по
`web_app/backend/.env.production.example`. Реальный файл не коммитить.

Обязательны:

- `DATABASE_URL` для отдельной web-схемы Postgres;
- `CREDITS_DB_URL` общей БД public-бота;
- `REDIS_URL` доступного с blast-ops Redis;
- `ORCHESTRATOR_PUBLIC_URL` фактического orchestrator ingress;
- Timeweb S3 endpoint, access key, secret, region и оба bucket;
- T-Банк terminal/password и существующий `TBANK_NOTIFY_URL` public-бота;
- отдельный Telegram bot token для web-login, чтобы не конфликтовать с polling public/team;
- Google OAuth client id/secret;
- TikTok Login Kit/Content Posting client key/secret;
- случайный Fernet `TIKTOK_TOKEN_KEY`;
- случайный `BLAST_SESSION_SECRET` длиной не менее 32 символов;
- mapping’и subtitle mode и footage artist id;
- три каталога с реальными `https://` или `s3://` preview: footage, photo, subtitle.

Имена элементов catalog обязаны присутствовать в соответствующем mapping. Для `s3://`
объектов startup выполняет `HeadObject`; несуществующая картинка/видео блокирует запуск.

## Внешние кабинеты

1. Google Cloud:
   - redirect URI: `https://app.blast808.com/api/auth/google/callback`;
   - policy: `https://app.blast808.com/legal/policy`;
   - offer: `https://app.blast808.com/legal/offer`;
   - приложение переведено из Testing в Production.
2. TikTok Developers:
   - redirect URI: `https://app.blast808.com/api/tiktok/callback`;
   - scopes: `user.info.basic,video.list,video.publish,video.upload`;
   - legal URLs доступны без авторизации.
3. DNS/TLS:
   - `app.blast808.com` указывает на blast-ops;
   - сертификаты существуют в `/etc/letsencrypt/live/app.blast808.com/`.

## GitHub

Подготовить env и записать его одним secret:

```bash
base64 < web_app/backend/.env.production | tr -d '\n' \
  | gh secret set BLAST_WEB_PRODUCTION_ENV_B64
```

Первый деплой запускать вручную из `Deploy Blast Web Production`. После полного E2E
включить автодеплой:

```bash
gh variable set BLAST_WEB_PROD_AUTO_DEPLOY --body true
```

Workflow preview оставлен только в `workflow_dispatch`, поэтому push в `main` больше не
может случайно вернуть mock поверх production.

## Проверка перед первым запуском

```bash
python3 -m pytest \
  tests/test_web_production_backend.py \
  tests/test_tbank_card_list.py \
  tests/test_render_job_template_lifecycle.py -q

cd web_app/frontend
npm run typecheck
npm run i18n:check
npm run build
```

Production compose должен проходить config и обе сборки с настоящим env:

```bash
BLAST_WEB_ENV_FILE="$PWD/web_app/backend/.env.production" \
docker compose -f web_app/docker-compose.production.yml config >/dev/null

BLAST_WEB_ENV_FILE="$PWD/web_app/backend/.env.production" \
docker compose -f web_app/docker-compose.production.yml build --pull
```

## Приёмочный smoke

1. `GET /healthz` возвращает `backend=production`.
2. `POST /api/dev/login` возвращает 404.
3. Регистрация через Telegram проходит с одного `/start`; Google login возвращает в SPA.
4. Создание Blast order возвращает T-Банк URL, но до webhook баланс не меняется.
5. Подтверждённая оплата видна и в web, и в public-боте.
6. Трек загружается в Timeweb S3; job проходит web → orchestrator → Windows → S3.
7. Повторная вариация использует Stage 1 первой; ошибка возвращает только её кредиты.
8. Download отдаётся как attachment.
9. TikTok connect → publish → status → Display metrics обновляет проект и stats.
10. Cancel/resume Blast меняет фактическую подписку; Glow/Impulse не показывают cancel.
11. Удаление аккаунта удаляет web data и пользовательские S3-объекты.
12. В Loki/Dozzle видны `blast-web-api` и `blast-web-frontend`, секреты и полный текст
    трека в общие логи не попадают.

## Намеренно не входит в первый production

- пользовательские video/photo sources: текущий orchestrator contract их не принимает;
- автоматические кандидаты drop timing на сайте: остаётся ручной тайминг в FX;
- server-side composite preview: UI использует локальное безопасное preview;
- ручная бонусная шкала: общая credits DB сама начисляет track credits по платежам;
- динамический subtitle preview через отдельный render endpoint.

Эти controls скрыты capability-флагами в production. Backend также возвращает явную
ошибку для прямого запроса неподдерживаемой операции; mock-ответа в production нет.

## Решение о переключении

Production включается только когда одновременно выполнены четыре условия:

- заполнен и сохранён `BLAST_WEB_PRODUCTION_ENV_B64`;
- загружены и проверены все preview objects;
- Google/TikTok callbacks подтверждены во внешних кабинетах;
- ручной E2E дошёл до готового Windows-render и подтверждённой тестовой оплаты.
