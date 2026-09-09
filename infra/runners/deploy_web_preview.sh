#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${GITHUB_WORKSPACE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
COMPOSE_FILE="$ROOT_DIR/web_app/docker-compose.preview.yml"
HTTP_CONF="$ROOT_DIR/infra/runners/nginx/app.blast808.com.http.conf"
TLS_CONF="$ROOT_DIR/infra/runners/nginx/app.blast808.com.conf"
DOMAIN="app.blast808.com"
HOST_PORT="18190"

: "${BLAST_WEB_SESSION_SECRET:?BLAST_WEB_SESSION_SECRET is required}"

host_exec() {
  docker run --rm --privileged --pid host --network host -v /:/host alpine:3.20 \
    chroot /host "$@"
}

install_nginx_conf() {
  local source_file="$1"
  # The checkout lives inside the runner container and is not a host path from
  # the Docker daemon's point of view. Stream the file and only bind host dirs.
  docker run --rm -i \
    -v /etc/nginx/sites-available:/dst \
    alpine:3.20 sh -euc \
    'cat > /dst/app.blast808.com.conf' < "$source_file"
  docker run --rm -v /etc/nginx/sites-enabled:/dst alpine:3.20 \
    ln -sfn /etc/nginx/sites-available/app.blast808.com.conf /dst/app.blast808.com.conf
}

wait_http() {
  local url="$1"
  local attempts="${2:-60}"
  for ((i = 1; i <= attempts; i++)); do
    if docker run --rm --network host curlimages/curl:8.12.1 \
      -fsS --max-time 8 "$url" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  echo "web preview did not become ready: $url" >&2
  return 1
}

export BLAST_WEB_IMAGE_TAG="${GITHUB_SHA:-local}"
docker compose --project-name blast-web-preview -f "$COMPOSE_FILE" build --pull
docker compose --project-name blast-web-preview -f "$COMPOSE_FILE" up -d
wait_http "http://127.0.0.1:${HOST_PORT}/healthz" 90

if ! host_exec test -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"; then
  install_nginx_conf "$HTTP_CONF"
  host_exec nginx -t
  host_exec systemctl reload nginx

  docker run --rm \
    -v /etc/letsencrypt:/etc/letsencrypt \
    -v /var/www/blast808.com:/var/www/blast808.com \
    certbot/certbot:latest certonly \
      --webroot --webroot-path /var/www/blast808.com \
      --domain "$DOMAIN" \
      --cert-name "$DOMAIN" \
      --non-interactive --agree-tos --register-unsafely-without-email
fi

install_nginx_conf "$TLS_CONF"
host_exec nginx -t
host_exec systemctl reload nginx

wait_http "https://${DOMAIN}/healthz" 90

dev_status="$(docker run --rm --network host curlimages/curl:8.12.1 \
  -ksS -o /dev/null -w '%{http_code}' \
  --resolve "${DOMAIN}:443:127.0.0.1" \
  -X POST "https://${DOMAIN}/api/dev/login")"
if [[ "$dev_status" != "404" ]]; then
  echo "preview security check failed: /api/dev/login status=$dev_status" >&2
  exit 1
fi

docker compose --project-name blast-web-preview -f "$COMPOSE_FILE" ps
echo "[web-preview] ready url=https://${DOMAIN}"
