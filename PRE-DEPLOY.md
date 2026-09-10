# Blast Web: production readiness

Актуально на 5 сентября 2026. Канонический домен приложения:
`https://app.blast808.com`.

## Статус

Домен уже работает на production-контуре (`MODE=prod`,
`BLAST_BACKEND_MODE=production`). Telegram-вход включён. Google и TikTok остаются
скрытыми, пока для них не заполнены полные пары credentials во внешних кабинетах.

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
- Трек, изображения профиля, прогрев F1/F6 и пользовательские исходники загружаются
  в Timeweb S3. Загрузка с телефона использует десятиминутную project-bound ссылку.
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
- Геометрия сохраняется для каждой вариации отдельно: 9:16, 16:9 и фото 4:3
  не приводятся к общей форме. Визуальные контейнеры wizard подстраиваются под
  формат кадра, включая 16:9 без пустых краёв.
- Субтитры и эффекты показывают готовые production-превью из S3. FX-каталог проверяется
  при старте вместе с остальными preview objects.
- Бонусы Blast записываются в общую credits DB атомарно: +1 трек после первого и
  второго полного месяца, безлимит треков после третьего.
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
- Google OAuth client id/secret, если провайдер включается;
- TikTok Login Kit/Content Posting client key/secret, если провайдер включается;
- случайный Fernet `TIKTOK_TOKEN_KEY`;
- случайный `BLAST_SESSION_SECRET` длиной не менее 32 символов;
- mapping’и subtitle mode и footage artist id, включая явный
  `WEB_DEFAULT_FOOTAGE_ARTIST_ID`;
- четыре каталога с реальными `https://` или `s3://` preview: footage, photo,
  subtitle и fx.

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

## Хранение production env

Production env хранится только на `blast-ops`:

```bash
install -m 600 /dev/null /home/deploy/blast_final/web_app/backend/.env.production
# заполнить файл по web_app/backend/.env.production.example
```

Workflow читает файл по постоянному пути
`/home/deploy/blast_final/web_app/backend/.env.production`; `actions/checkout` его не
создаёт и не удаляет. Файл игнорируется Git и переживает обычный prod/infra deploy.

Локальная резервная копия лежит по тому же относительному пути
`web_app/backend/.env.production`, имеет права `600` и также игнорируется Git. После
изменения одной копии вторую обновлять вручную по защищённому каналу.

Первый деплой запускать вручную из `Deploy Blast Web Production`. GitHub secrets для
web env не используются. После полного E2E при необходимости включить автодеплой:

```bash
gh variable set BLAST_WEB_PROD_AUTO_DEPLOY --body true
```

Workflow preview оставлен только в `workflow_dispatch`, поэтому push в `main` больше не
может случайно вернуть mock поверх production.

## Проверка перед первым запуском

```bash
python3 -m pytest \
  tests/test_web_production_backend.py \
  tests/test_web_custom_sources.py \
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
   Отдельно проверить F1-аудио, F6-видео и свои исходники с сохранением порядка.
7. Повторная вариация использует Stage 1 первой; ошибка возвращает только её кредиты.
8. Download отдаётся как attachment.
9. TikTok connect → publish → status → Display metrics обновляет проект и stats.
10. Cancel/resume Blast меняет фактическую подписку; Glow/Impulse не показывают cancel.
    Заработанный бонус меняет общий лимит треков и не начисляется повторно.
11. Удаление аккаунта удаляет web data и пользовательские S3-объекты.
12. В Loki/Dozzle видны `blast-web-api` и `blast-web-frontend`, секреты и полный текст
    трека в общие логи не попадают.

## Границы текущего production

- композит субтитров и эффекта не рендерится интерактивно: UI показывает два готовых
  примера отдельно;
- TikTok и Google доступны только после настройки и ревью соответствующего провайдера;
- пользовательские исходники принимаются как MP4-видео 9:16 или 16:9; фото остаются
  библиотечным production-планом 4:3.

## Решение о переключении

Следующий релиз принимается только когда одновременно выполнены четыре условия:

- заполнен серверный `/home/deploy/blast_final/web_app/backend/.env.production`;
- загружены и проверены все preview objects;
- Google/TikTok callbacks подтверждены во внешних кабинетах;
- ручной E2E дошёл до готового Windows-render и подтверждённой тестовой оплаты.
