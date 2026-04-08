# CI/CD + Runners

Цель: `push` в любую ветку репозитория -> на сервере подхватываем эту же ветку -> `docker compose up -d --build`.

## Что добавлено

- Workflow: `.github/workflows/deploy-current-branch.yml`
- Скрипт деплоя: `infra/runners/deploy_branch.sh`
- Docker Compose для GitHub self-hosted runner: `infra/runners/docker-compose.github-runner.yml`
- Docker Compose для web UI логов (Dozzle): `infra/runners/docker-compose.logs.yml`
- Docker Compose для observability V1: `infra/runners/docker-compose.observability.yml`
- Пример env: `infra/runners/.env.github-runner.example`
- Пример env для Dozzle: `infra/runners/.env.dozzle.example`
- Пример env для observability: `infra/runners/.env.observability.example`

## 1) Поднять self-hosted runner

На сервере:

```bash
cd /opt/blast_mj_final/infra/runners
cp .env.github-runner.example .env.github-runner
# отредактируй GH_RUNNER_ACCESS_TOKEN и BLAST_REPO_DIR
docker compose -f docker-compose.github-runner.yml --env-file .env.github-runner up -d
```

Runner поднимется с label: `self-hosted,blast-deploy`.

## 2) Настроить GitHub variable

В GitHub репозитории добавь `Repository variable`:

- `BLAST_REPO_DIR=/opt/blast_mj_final`

Workflow использует эту переменную, чтобы выполнить деплой в постоянном клоне репозитория на сервере.

## 3) Как работает деплой

При `push` в ветку `X`:

1. Workflow запускается на self-hosted runner.
2. Вызывает `infra/runners/deploy_branch.sh X`.
3. Скрипт делает:
   - `git fetch`
   - `git checkout X`
   - `git pull --ff-only`
   - `docker compose up -d --build`

## 3.1) Опционально: отдельный деплой `landing/` в Ubuntu nginx

Если прод-домен обслуживается локальным nginx (а не S3/CDN), можно включить отдельный sync после deploy:

- Скрипт: `infra/runners/deploy_landing_to_nginx.sh`
- Workflow step: `.github/workflows/deploy-current-branch.yml` (`Deploy Landing To Nginx (optional)`)

Нужные `Repository variables`:

- `LANDING_NGINX_DEPLOY_ENABLED=true`
- `LANDING_NGINX_DOCROOT=/var/www/blast808.com` (пример)
- `LANDING_NGINX_MAIN_ONLY=true` (рекомендуется)
- `LANDING_NGINX_MAIN_BRANCH=main`
- `LANDING_NGINX_RELOAD_CMD=sudo nginx -t && sudo systemctl reload nginx` (опционально)
- `LANDING_NGINX_SYNC_MODE=auto` (или `docker-host`, если runner работает в Docker)

Поведение:

- синк `REPO_DIR/landing/` -> `LANDING_NGINX_DOCROOT` через `rsync -a --delete`
- `sync_mode=auto` пытается использовать `docker-host`, если доступен `/var/run/docker.sock`
- исключает `*.rar` и `tmp/`
- по умолчанию обновляет только `main`
- проверяет маркер в `index.html` после синка

## 4) Web UI логов по всем контейнерам (Dozzle через nginx auth)

На сервере:

```bash
cd /opt/blast_mj_final/infra/runners
cp .env.dozzle.example .env.dozzle

docker compose -f docker-compose.logs.yml --env-file .env.dozzle up -d
```

Рекомендованный режим:
- `DOZZLE_BIND_HOST=127.0.0.1` (не публиковать Dozzle напрямую наружу)
- `DOZZLE_BASE=/logs`
- доступ только через nginx reverse-proxy с Basic Auth, например `https://blast808.com/logs/`

Для панели бота аналогично: `https://blast808.com/admin/` (также через Basic Auth).
`asset-ui` рекомендуется прокинуть в той же зоне: `https://blast808.com/admin/assets/`.
При каждом deploy (`docker compose up -d --build`) `asset-ui` пересобирается с
новым frontend (`asset_ui/dist`) автоматически.

Dozzle показывает live-логи Docker-контейнеров (включая воркеры/бота/API) и удобен как легкий старт.

## Loki vs Dozzle

- `Dozzle` — быстрый live-просмотр логов контейнеров (без тяжелой настройки).
- `Loki + Grafana` — если нужна ретенция, сложные запросы, дашборды, алерты.

Если цель сейчас "видеть все логи здесь и сейчас", Dozzle — хороший первый шаг.

## 5) Observability V1 (Loki + Prometheus + Grafana + Alertmanager + Promtail)

Цель V1:
- Dozzle остается для live tail.
- Долговременное хранение логов + запросы + алерты переносим в Loki/Prometheus/Grafana/Alertmanager.

На сервере:

```bash
cd /opt/blast_mj_final/infra/runners
cp .env.observability.example .env.observability
# отредактируй:
# - GRAFANA_ADMIN_PASSWORD
# - BLAST_DOCKER_NETWORK (сеть docker compose основного контура)
# - ALERT_TELEGRAM_BOT_TOKEN / ALERT_TELEGRAM_CHAT_ID
# - (для доступа снаружи через nginx subpath — обязательно)
#   GRAFANA_ROOT_URL=https://blast808.com/admin/obs/grafana/
#   GRAFANA_SERVE_FROM_SUB_PATH=true
#   PROMETHEUS_EXTERNAL_URL=https://blast808.com/admin/obs/prometheus/
#   ALERTMANAGER_EXTERNAL_URL=https://blast808.com/admin/obs/alertmanager/
#   Иначе Grafana может логинить успешно, но редиректить на "/" и давать 404.

docker compose -f docker-compose.observability.yml --env-file .env.observability up -d
```

Проверка:
- `http://127.0.0.1:19090/targets` — Prometheus видит `orchestrator-api`.
- `http://127.0.0.1:13000` — Grafana (дешборды под папкой `Blast Observability`).
- `http://127.0.0.1:19093` — Alertmanager.
- `http://127.0.0.1:13100/ready` — Loki ready.

Важно:
- Orchestrator должен экспортировать `GET /metrics/prometheus`.
- `promtail` читает Docker logs и метит минимум `service`, `container`, `env`; `job_id` извлекается regex-пайплайном.
- Для production рекомендуется закрыть порты и публиковать Grafana/Prometheus/Alertmanager только через nginx + auth.

### 5.1) Публикация через `blast808.com` + Basic Auth

Готовый пример location-блоков:
- `infra/runners/nginx/observability.locations.conf.example`

Рекомендуемые внешние URL:
- `https://blast808.com/admin/obs/grafana/`
- `https://blast808.com/admin/obs/prometheus/`
- `https://blast808.com/admin/obs/alertmanager/`

После внесения nginx-конфига:

```bash
sudo nginx -t && sudo systemctl reload nginx
```
