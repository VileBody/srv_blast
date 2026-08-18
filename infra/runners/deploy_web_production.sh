#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${GITHUB_WORKSPACE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
COMPOSE_FILE="$ROOT_DIR/web_app/docker-compose.production.yml"
PREVIEW_COMPOSE_FILE="$ROOT_DIR/web_app/docker-compose.preview.yml"
NGINX_CONF="$ROOT_DIR/infra/runners/nginx/app.blast808.com.conf"
ENV_FILE="$ROOT_DIR/web_app/backend/.env.production"
DOMAIN="app.blast808.com"
HOST_PORT="18190"

: "${BLAST_WEB_PRODUCTION_ENV_B64:?BLAST_WEB_PRODUCTION_ENV_B64 is required}"

cleanup() {
  rm -f "$ENV_FILE"
}
trap cleanup EXIT

printf '%s' "$BLAST_WEB_PRODUCTION_ENV_B64" | base64 --decode > "$ENV_FILE"
chmod 600 "$ENV_FILE"

required=(
  MODE BLAST_BACKEND_MODE APP_URL BLAST_CORS_ORIGINS BLAST_SESSION_SECRET
  DATABASE_URL CREDITS_DB_URL REDIS_URL ORCHESTRATOR_PUBLIC_URL
  S3_ENDPOINT_URL S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY S3_REGION
  S3_BUCKET_RAW_AUDIO S3_RAW_AUDIO_PREFIX S3_BUCKET_ASSET_STORAGE S3_WEB_ASSET_PREFIX
  TBANK_TERMINAL_KEY TBANK_PASSWORD TBANK_NOTIFY_URL
  TELEGRAM_BOT_TOKEN TELEGRAM_BOT_USERNAME
  GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET GOOGLE_REDIRECT_URI
  TIKTOK_CLIENT_KEY TIKTOK_CLIENT_SECRET TIKTOK_REDIRECT_URI TIKTOK_TOKEN_KEY TIKTOK_UPLOAD_SOURCE
  WEB_STAGE1_ALIGNMENT_BACKEND WEB_SUBTITLE_MODE_MAP_JSON WEB_FOOTAGE_ARTIST_MAP_JSON
  WEB_FOOTAGE_CATALOG_JSON WEB_PHOTO_CATALOG_JSON WEB_SUBTITLE_CATALOG_JSON
)

for name in "${required[@]}"; do
  if ! grep -Eq "^${name}=.+" "$ENV_FILE"; then
    echo "production env is missing $name" >&2
    exit 1
  fi
done

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

export BLAST_WEB_IMAGE_TAG="${GITHUB_SHA:-local}"
export BLAST_WEB_ENV_FILE="$ENV_FILE"
docker compose --project-name blast-web -f "$COMPOSE_FILE" config >/dev/null
docker compose --project-name blast-web -f "$COMPOSE_FILE" build --pull

preview_running=0
if docker inspect -f '{{.State.Running}}' blast-web-preview-frontend 2>/dev/null | grep -qx true; then
  preview_running=1
  docker compose --project-name blast-web-preview -f "$PREVIEW_COMPOSE_FILE" stop web
fi

rollback_preview() {
  if [[ "$preview_running" == "1" ]]; then
    docker compose --project-name blast-web -f "$COMPOSE_FILE" stop web api || true
    docker compose --project-name blast-web-preview -f "$PREVIEW_COMPOSE_FILE" start api web || true
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
docker compose --project-name blast-web-preview -f "$PREVIEW_COMPOSE_FILE" down --remove-orphans || true
docker compose --project-name blast-web -f "$COMPOSE_FILE" ps
echo "[web-production] ready url=https://${DOMAIN}"
