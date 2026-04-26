# CI/CD + Runners

Цель: `push` в любую ветку репозитория -> на сервере подхватываем эту же ветку -> `docker compose up -d --build`.

## Что добавлено

- Workflow: `.github/workflows/deploy-current-branch.yml`
- Workflow (split): `.github/workflows/deploy-split-main.yml`
- Скрипт деплоя: `infra/runners/deploy_branch.sh`
- Скрипт удаленного деплоя на prod VM по SSH: `infra/runners/deploy_remote_branch.sh`
- Скрипт sync orchestrator nginx snippets: `infra/runners/deploy_orchestrator_nginx.sh`
- Скрипт remote sync orchestrator nginx snippets по SSH: `infra/runners/deploy_orchestrator_nginx_remote.sh`
- Docker Compose для GitHub self-hosted runner: `infra/runners/docker-compose.github-runner.yml`
- Docker Compose для web UI логов (Dozzle): `infra/runners/docker-compose.logs.yml`
- Docker Compose для observability V1: `infra/runners/docker-compose.observability.yml`
- Docker Compose для prod-node log shipping: `infra/runners/docker-compose.promtail-edge.yml`
- Пример env: `infra/runners/.env.github-runner.example`
- Пример env для Dozzle: `infra/runners/.env.dozzle.example`
- Пример env для observability: `infra/runners/.env.observability.example`
- Пример env для prod-node promtail: `infra/runners/.env.promtail-edge.example`

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
   - `docker compose up -d --build` (или stack-aware режим)

### 3.2) Stack-aware deploy (prod-path / infra-ops)

`infra/runners/deploy_branch.sh` поддерживает второй аргумент:

- `all` (по умолчанию): legacy single-node deploy.
- `prod-path`: `orchestrator-api`, `worker-build`, `worker-render`, `tg-bot-public` + опционально `orchestrator-api-2` (если `DEPLOY_ORCHESTRATOR_HA=true`) + опционально Dozzle agent (если есть `.env.dozzle-agent`) + опционально `promtail-edge`.
- `infra-apps`: `tg-bot`, `asset-ui`, `finance-bot`.
- `infra-ops`: `infra-apps` + `dozzle` + `observability` + `github-runner` (если есть соответствующие `.env`).

Примеры:

```bash
bash infra/runners/deploy_branch.sh main prod-path
bash infra/runners/deploy_branch.sh main infra-ops
```

Опция:

- `DEPLOY_PRUNE_OTHER_STACK=true` — после деплоя останавливает сервисы противоположного stack.

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

## 3.3) Orchestrator HA: 2 API инстанса + nginx роутер

Для отказоустойчивого входа можно поднять реплику `orchestrator-api` и балансировать
входящий трафик через локальный nginx на той же VM.

Что добавлено в репозиторий:
- Compose override: `docker-compose.orchestrator-ha.yml` (сервис `orchestrator-api-2`).
- Nginx snippets: `infra/runners/nginx/orchestrator.upstream.conf.example` (в `http {}`) и `infra/runners/nginx/orchestrator.locations.conf.example` (в `server {}`).

Важно:
- `REDIS_HOST`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` должны оставаться общими.
- На реплике принудительно выставлен `ALERT_SUBSCRIBERS_ENABLED=0`, чтобы не дублировать long-polling ops-alert бота.
- В `MODE=prod` выставляй `ORCHESTRATOR_PUBLIC_URL` на nginx/vhost endpoint, а не на конкретный контейнер.
- Для CI включение HA делается через `Repository variable`: `DEPLOY_ORCHESTRATOR_HA=true`.

Поднять primary + replica:

```bash
cd /opt/blast_mj_final
docker compose -f docker-compose.yml -f docker-compose.orchestrator-ha.yml up -d --build orchestrator-api orchestrator-api-2
```

Проверка:

```bash
curl -fsS http://127.0.0.1:18000/health
curl -fsS http://127.0.0.1:18001/health
sudo nginx -t && sudo systemctl reload nginx
```

Опциональный sync nginx snippets через CI:
- workflow step: `Deploy Orchestrator Nginx (optional)` в обоих workflow (`deploy-current-branch.yml` и `deploy-split-main.yml`, prod job).
- скрипт: `infra/runners/deploy_orchestrator_nginx.sh`.

Нужные `Repository variables` для этого шага:
- `ORCHESTRATOR_NGINX_DEPLOY_ENABLED=true`
- `ORCHESTRATOR_NGINX_MAIN_ONLY=true` (рекомендуется)
- `ORCHESTRATOR_NGINX_MAIN_BRANCH=main`
- `ORCHESTRATOR_NGINX_UPSTREAM_TARGET=/etc/nginx/conf.d/blast_orchestrator_upstream.conf`
- `ORCHESTRATOR_NGINX_LOCATIONS_TARGET=/etc/nginx/snippets/blast_orchestrator.locations.conf`
- `ORCHESTRATOR_NGINX_RELOAD_CMD=sudo nginx -t && sudo systemctl reload nginx`

Опционально (если шаблоны лежат не в дефолтных путях репо):
- `ORCHESTRATOR_NGINX_UPSTREAM_TEMPLATE=/opt/blast_mj_final/infra/runners/nginx/orchestrator.upstream.conf.example`
- `ORCHESTRATOR_NGINX_LOCATIONS_TEMPLATE=/opt/blast_mj_final/infra/runners/nginx/orchestrator.locations.conf.example`

Для удаленного обновления nginx на orchestrator VM из CI (когда runner не имеет прямого доступа к host `/etc/nginx`):
- `ORCHESTRATOR_NGINX_REMOTE_ENABLED=true`
- `ORCHESTRATOR_NGINX_REMOTE_HOST=<orchestrator-ip>`
- `ORCHESTRATOR_NGINX_REMOTE_USER=blast` (или другой sudo-user)
- `ORCHESTRATOR_NGINX_REMOTE_PORT=22`
- `Repository secret ORCHESTRATOR_NGINX_REMOTE_SSH_PRIVATE_KEY` (private key для SSH на orchestrator VM)

При включенном `ORCHESTRATOR_NGINX_REMOTE_ENABLED=true` workflow использует
`deploy_orchestrator_nginx_remote.sh`: шаблоны из git-копии workflow передаются по SSH
на orchestrator VM, устанавливаются в target path и затем выполняется `ORCHESTRATOR_NGINX_RELOAD_CMD`.

## 4) Web UI логов по всем контейнерам (Dozzle через nginx auth)

На `blast-ops`:

```bash
cd /opt/blast_mj_final/infra/runners
cp .env.dozzle.example .env.dozzle

docker compose -f docker-compose.logs.yml --env-file .env.dozzle up -d
```

Рекомендованный режим:
- `DOZZLE_BIND_HOST=127.0.0.1` (не публиковать Dozzle напрямую наружу)
- `DOZZLE_BASE=/logs`
- `DOZZLE_HOSTNAME=blast-ops`
- `DOZZLE_REMOTE_AGENT=` пустой для single-host или явный список `<node-private-ip>:7007,<node-private-ip>:7007`
- доступ только через nginx reverse-proxy с Basic Auth, например `https://blast808.com/logs/`

На `orchestrator-0/1` Dozzle live-tail подключается через lightweight agent.
В `prod-path` деплое `deploy_branch.sh` сам создает `infra/runners/.env.dozzle-agent`,
если файла еще нет: bind host берется из private IP ноды, hostname — из
`ORCHESTRATOR_NODE_NAME`, порт — `7007`.

```bash
cd /opt/blast_mj_final/infra/runners
cp .env.dozzle-agent.example .env.dozzle-agent

docker compose -f docker-compose.dozzle-agent.yml --env-file .env.dozzle-agent up -d
```

Для каждой orchestrator-ноды нужно явно задать private bind host и hostname в локальном
`infra/runners/.env.dozzle-agent`. Пример:

```bash
DOZZLE_AGENT_BIND_HOST=<this-node-private-ip>
DOZZLE_AGENT_PORT=7007
DOZZLE_AGENT_HOSTNAME=<this-node-name>
DOZZLE_AGENT_LEVEL=info
```

В `deploy_branch.sh prod-path` agent поднимается на каждой prod-ноде. Если env
отсутствует, deploy создаст его с non-secret значениями. Если bind host указывает
на loopback/`0.0.0.0`, deploy завершится ошибкой.

Для старых `.env.dozzle` deploy-скрипт дописывает только non-secret значения
`DOZZLE_AUTH_PROVIDER=none`, `DOZZLE_BASE=/logs`, `DOZZLE_HOSTNAME=blast-ops`.
`DOZZLE_BIND_HOST` и `DOZZLE_PORT` остаются обязательными в локальном env.
Если `DOZZLE_REMOTE_AGENT` пустой, CI/CD заполняет текущий split-prod список:
`192.168.0.8:7007,192.168.0.11:7007`.

Для панели бота аналогично: `https://blast808.com/admin/` (также через Basic Auth).
`asset-ui` рекомендуется прокинуть в той же зоне: `https://blast808.com/admin/assets/`.
При каждом deploy (`docker compose up -d --build`) `asset-ui` пересобирается с
новым frontend (`asset_ui/dist`) автоматически.

Dozzle показывает live-логи Docker-контейнеров на `blast-ops`; при заполненном
`DOZZLE_REMOTE_AGENT` он также подключает другие Docker hosts через remote agents.

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

### 5.2) Split режим: Loki на infra VM, логи с prod VM через promtail-edge

На **prod VM** поднимается только `promtail-edge`, который читает docker logs локально и шлет в Loki на infra VM:

```bash
cd /opt/blast_mj_final/infra/runners
cp .env.promtail-edge.example .env.promtail-edge
# заполни PROMTAIL_LOKI_URL и labels OBS_NODE_*
docker compose -f docker-compose.promtail-edge.yml --env-file .env.promtail-edge up -d
```

Важно:

- на infra VM Loki должен быть доступен prod VM по сети (private IP + firewall allowlist).
- это pull-like логирование со стороны prod узла: приложения не отправляют логи в Loki напрямую.

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

## 6) CI/CD split rollout (рекомендуется)

Для двух VM используем два self-hosted runner:

- `blast-deploy-prod` (на prod VM)
- `blast-deploy-infra` (на infra VM)

Workflow:

- `.github/workflows/deploy-split-main.yml`

Repository variables:

- `DEPLOY_SPLIT_ENABLED=true`
- `BLAST_REPO_DIR_PROD=/opt/blast_mj_final` (или ваш путь на prod VM)
- `BLAST_REPO_DIR_INFRA=/opt/blast_mj_final` (или ваш путь на infra VM)
- `DEPLOY_PRUNE_OTHER_STACK=true` (опционально)
- `DEPLOY_ORCHESTRATOR_HA=true` (опционально, включает `docker-compose.orchestrator-ha.yml` в `prod-path`)
- `DEPLOY_ORCHESTRATOR_HA_COMPOSE_FILE=docker-compose.orchestrator-ha.yml` (опционально)

Для схемы "runner на orchestrator VM, prod-path на отдельной blast-ops VM":
- `DEPLOY_PROD_REMOTE_ENABLED=true`
- `DEPLOY_PROD_REMOTE_HOST=<prod-vm-ip>`
- `DEPLOY_PROD_REMOTE_USER=deploy`
- `DEPLOY_PROD_REMOTE_PORT=22` (или ваш SSH port)
- `DEPLOY_PROD_REMOTE_REPO_DIR=/home/deploy/blast_final`
- `Repository secret DEPLOY_PROD_SSH_PRIVATE_KEY` (private key для `deploy@prod-vm`)

При включенном `DEPLOY_PROD_REMOTE_ENABLED=true` job `deploy-prod-path` выполняет deploy по SSH
на prod VM, а не локально на runner-хосте.

Legacy workflow `.github/workflows/deploy-current-branch.yml` автоматически пропускается при `DEPLOY_SPLIT_ENABLED=true`.

## 7) Logs Backup V2 (PostgreSQL normalization + S3 raw backup)

Что добавлено:

- SQL схема: `infra/logging/sql/001_logs_schema.sql`
- Pipeline CLI: `scripts/logs_pipeline.py`
- Systemd units: `infra/logging/systemd/*.service|*.timer`
- Watchdog workflow: `.github/workflows/logs-watchdog.yml`
- Пример env: `infra/runners/.env.logs-backup.example`

### 7.1 Централизованный режим (рекомендуется)

```bash
cd /opt/blast_mj_final/infra/runners
cp .env.logs-backup.example .env.logs-backup
# заполни LOG_BACKUP_* + S3_* значения
```

Важно:
- `LOG_BACKUP_ENABLED=true`
- `LOG_BACKUP_MODE=centralized`
- `LOG_BACKUP_DB_DSN` указывает в тот же Postgres (схема `logs`)
- `LOG_BACKUP_S3_BUCKET` = рабочий bucket (обычно `S3_BUCKET_ASSET_STORAGE`)
- logs-service VM (`blast-ops`): `LOG_BACKUP_NODE_ROLE=logs-service`, `LOG_BACKUP_LOKI_ENABLED=true`, `LOG_BACKUP_DOCKER_ENABLED=false`
- prod VM (`orchestrator`): не включаем logs pipeline (`LOG_BACKUP_ENABLED=false`/без `.env.logs-backup`)
- все контейнерные логи с prod/infra сходятся в Loki через `promtail`/`promtail-edge`, поэтому backup делается централизованно с logs-service.

Fail-fast контракт:
- при `LOG_BACKUP_MODE=centralized` pipeline завершится ошибкой, если `LOG_BACKUP_NODE_ROLE != logs-service`
- при `LOG_BACKUP_MODE=centralized` pipeline завершится ошибкой, если `LOG_BACKUP_LOKI_ENABLED != true` или `LOG_BACKUP_DOCKER_ENABLED != false`

### 7.2 Автоустановка systemd через deploy

`infra/runners/deploy_branch.sh` автоматически:
- копирует `infra/runners/.env.logs-backup` -> `/etc/blast/logs-backup.env`
- устанавливает units в `/etc/systemd/system/`
- включает таймеры `blast-logs-hourly.timer` и `blast-logs-prune.timer`

Условие: `LOG_BACKUP_ENABLED=true` в `.env.logs-backup`.
При `LOG_BACKUP_MODE=centralized` deploy тоже завершится ошибкой, если `LOG_BACKUP_NODE_ROLE != logs-service`.

### 7.3 Ручные команды (one-shot)

```bash
cd /opt/blast_mj_final
set -a; . /etc/blast/logs-backup.env; set +a

python3 scripts/logs_pipeline.py migrate
python3 scripts/logs_pipeline.py bootstrap-s3-lifecycle
python3 scripts/logs_pipeline.py backfill --days 30
python3 scripts/logs_pipeline.py healthcheck --max-lag-min 90
```

### 7.4 Watchdog

`logs-watchdog.yml` запускается по расписанию на logs-service runner (`blast-deploy-infra`):
- проверяет lag через `healthcheck`
- при проблеме выполняет `systemctl start blast-logs-hourly.service`
- фейлит job, чтобы инцидент был виден в GitHub Actions
