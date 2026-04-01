# CI/CD + Runners

Цель: `push` в любую ветку репозитория -> на сервере подхватываем эту же ветку -> `docker compose up -d --build`.

## Что добавлено

- Workflow: `.github/workflows/deploy-current-branch.yml`
- Скрипт деплоя: `infra/runners/deploy_branch.sh`
- Docker Compose для GitHub self-hosted runner: `infra/runners/docker-compose.github-runner.yml`
- Docker Compose для web UI логов (Dozzle): `infra/runners/docker-compose.logs.yml`
- Пример env: `infra/runners/.env.github-runner.example`
- Пример env для Dozzle: `infra/runners/.env.dozzle.example`

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
