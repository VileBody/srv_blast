#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${GITHUB_WORKSPACE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
COMPOSE_FILE="$ROOT_DIR/web_app/docker-compose.production.yml"
PREVIEW_COMPOSE_FILE="$ROOT_DIR/web_app/docker-compose.preview.yml"
NGINX_CONF="$ROOT_DIR/infra/runners/nginx/app.blast808.com.conf"
ENV_FILE="${BLAST_WEB_PRODUCTION_ENV_FILE:-$ROOT_DIR/web_app/backend/.env.production}"
DOMAIN="app.blast808.com"
HOST_PORT="18190"

if [[ ! -s "$ENV_FILE" ]]; then
  echo "production env is missing or empty: $ENV_FILE" >&2
  exit 1
fi

env_mode="$(stat -c '%a' "$ENV_FILE")"
if (( (8#$env_mode & 077) != 0 )); then
  echo "production env must not be readable by group/others: $ENV_FILE mode=$env_mode" >&2
  exit 1
fi

required=(
  MODE BLAST_BACKEND_MODE APP_URL BLAST_CORS_ORIGINS BLAST_SESSION_SECRET
  DATABASE_URL CREDITS_DB_URL REDIS_URL ORCHESTRATOR_PUBLIC_URL
  S3_ENDPOINT_URL S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY S3_REGION
  S3_BUCKET_RAW_AUDIO S3_RAW_AUDIO_PREFIX S3_BUCKET_ASSET_STORAGE S3_WEB_ASSET_PREFIX
  TBANK_TERMINAL_KEY TBANK_PASSWORD TBANK_NOTIFY_URL
  TELEGRAM_BOT_TOKEN TELEGRAM_BOT_USERNAME
  GOOGLE_REDIRECT_URI
  TIKTOK_REDIRECT_URI TIKTOK_TOKEN_KEY TIKTOK_UPLOAD_SOURCE
  WEB_STAGE1_ALIGNMENT_BACKEND WEB_SUBTITLE_MODE_MAP_JSON WEB_FOOTAGE_ARTIST_MAP_JSON
  WEB_DEFAULT_FOOTAGE_ARTIST_ID WEB_FOOTAGE_CATALOG_JSON WEB_PHOTO_CATALOG_JSON
  WEB_SUBTITLE_CATALOG_JSON WEB_FX_CATALOG_JSON
)

for name in "${required[@]}"; do
  if ! grep -Eq "^${name}=.+" "$ENV_FILE"; then
    echo "production env is missing $name" >&2
    exit 1
  fi
done

# Google и TikTok — необязательные провайдеры: их кабинеты проходят ревью
# месяцами, а вход работает через Telegram, и кнопки на фронте гаснут сами по
# /api/auth/providers и /api/tiktok/status. Но ПОЛОВИНА конфигурации хуже её
# отсутствия — кнопка появится и приведёт человека на ошибку провайдера,
# поэтому либо обе переменные пары, либо ни одной. Тот же гейт, что в
# web_app/backend/app/runtime.py::_validate_optional_provider; разъехаться им
# нельзя, иначе деплой пропустит конфиг, на котором приложение не поднимется.
check_optional_pair() {
  local title="$1" first="$2" second="$3" have_first=0 have_second=0
  grep -Eq "^${first}=.+" "$ENV_FILE" && have_first=1
  grep -Eq "^${second}=.+" "$ENV_FILE" && have_second=1
  if (( have_first != have_second )); then
    echo "production env configures ${title} partially: set both ${first} and ${second}, or neither" >&2
    exit 1
  fi
}

check_optional_pair Google GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET
check_optional_pair TikTok TIKTOK_CLIENT_KEY TIKTOK_CLIENT_SECRET

if ! grep -Eq '^MODE=prod$' "$ENV_FILE" || ! grep -Eq '^BLAST_BACKEND_MODE=production$' "$ENV_FILE"; then
  echo "production env must explicitly select MODE=prod and BLAST_BACKEND_MODE=production" >&2
  exit 1
fi

if ! grep -Eq '^TIKTOK_UPLOAD_SOURCE=FILE_UPLOAD$' "$ENV_FILE"; then
  echo "production env must use TIKTOK_UPLOAD_SOURCE=FILE_UPLOAD until a Blast-owned media domain is verified" >&2
  exit 1
fi

wait_http() {
  local url="$1"
  local expected="$2"
  local attempts="${3:-90}"
  for ((i = 1; i <= attempts; i++)); do
    if body="$(docker run --rm --network host curlimages/curl:8.12.1 -fsS --max-time 10 "$url" 2>/dev/null)" \
      && grep -Fq "$expected" <<<"$body"; then
      return 0
    fi
    sleep 2
  done
  echo "production smoke failed: $url" >&2
  return 1
}

host_exec() {
  docker run --rm --privileged --pid host --network host -v /:/host alpine:3.20 \
    chroot /host "$@"
}

if ! host_exec test -s "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" \
  || ! host_exec test -s "/etc/letsencrypt/live/${DOMAIN}/privkey.pem"; then
  echo "production TLS certificate is missing for ${DOMAIN}" >&2
  exit 1
fi

docker run --rm -i -v /etc/nginx/sites-available:/dst alpine:3.20 sh -euc \
  'cat > /dst/app.blast808.com.conf' < "$NGINX_CONF"
docker run --rm -v /etc/nginx/sites-enabled:/dst alpine:3.20 \
  ln -sfn /etc/nginx/sites-available/app.blast808.com.conf /dst/app.blast808.com.conf
host_exec nginx -t
host_exec systemctl reload nginx

# Компоуз preview требует BLAST_WEB_SESSION_SECRET — это секрет preview-workflow,
# и в production-прогоне его нет. Здесь preview зовётся ТОЛЬКО для stop/start/down,
# то есть для команд жизненного цикла уже созданных контейнеров: значение переменной
# в них не участвует и в конфиг контейнера не попадает. Без заглушки `set -e` ронял
# деплой на первом же обращении к preview — раньше, чем production успевал подняться,
# и (что хуже) тем же способом сломался бы откат.
preview_compose() {
  BLAST_WEB_SESSION_SECRET="${BLAST_WEB_SESSION_SECRET:-lifecycle-only-unused}"     docker compose --project-name blast-web-preview -f "$PREVIEW_COMPOSE_FILE" "$@"
}

export BLAST_WEB_IMAGE_TAG="${GITHUB_SHA:-local}"
export BLAST_WEB_ENV_FILE="$ENV_FILE"
docker compose --project-name blast-web -f "$COMPOSE_FILE" config >/dev/null
docker compose --project-name blast-web -f "$COMPOSE_FILE" build --pull

preview_running=0
if docker inspect -f '{{.State.Running}}' blast-web-preview-frontend 2>/dev/null | grep -qx true; then
  preview_running=1
  preview_compose stop web
fi

rollback_preview() {
  if [[ "$preview_running" == "1" ]]; then
    docker compose --project-name blast-web -f "$COMPOSE_FILE" stop web api || true
    preview_compose start api web || true
  fi
}

if ! docker compose --project-name blast-web -f "$COMPOSE_FILE" up -d --remove-orphans; then
  rollback_preview
  exit 1
fi
if ! wait_http "http://127.0.0.1:${HOST_PORT}/healthz" '"backend":"production"'; then
  docker compose --project-name blast-web -f "$COMPOSE_FILE" logs --tail=200 api web >&2
  rollback_preview
  exit 1
fi

dev_status="$(docker run --rm --network host curlimages/curl:8.12.1 \
  -ksS -o /dev/null -w '%{http_code}' \
  --resolve "${DOMAIN}:443:127.0.0.1" \
  -X POST "https://${DOMAIN}/api/dev/login")"
if [[ "$dev_status" != "404" ]]; then
  echo "production security check failed: /api/dev/login status=$dev_status" >&2
  rollback_preview
  exit 1
fi

wait_http "https://${DOMAIN}/healthz" '"backend":"production"' 30
preview_compose down --remove-orphans || true
docker compose --project-name blast-web -f "$COMPOSE_FILE" ps
echo "[web-production] ready url=https://${DOMAIN}"
