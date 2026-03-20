# CI/CD + Runners

Цель: `push` в любую ветку репозитория -> на сервере подхватываем эту же ветку -> `docker compose up -d --build`.

## Что добавлено

- Workflow: `.github/workflows/deploy-current-branch.yml`
- Скрипт деплоя: `infra/runners/deploy_branch.sh`
- Docker Compose для GitHub self-hosted runner: `infra/runners/docker-compose.github-runner.yml`
- Docker Compose для web UI логов (Dozzle): `infra/runners/docker-compose.logs.yml`
- Пример env: `infra/runners/.env.github-runner.example`

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

## 4) Web UI логов по всем контейнерам

На сервере:

```bash
cd /opt/blast_mj_final/infra/runners
docker compose -f docker-compose.logs.yml up -d
```

UI: `http://SERVER_IP:18080` (или через SSH-туннель/реверс-прокси).

Dozzle показывает live-логи Docker-контейнеров (включая воркеры/бота/API) и удобен как легкий старт.

## Loki vs Dozzle

- `Dozzle` — быстрый live-просмотр логов контейнеров (без тяжелой настройки).
- `Loki + Grafana` — если нужна ретенция, сложные запросы, дашборды, алерты.

Если цель сейчас "видеть все логи здесь и сейчас", Dozzle — хороший первый шаг.

