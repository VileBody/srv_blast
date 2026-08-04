#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-${GITHUB_REF_NAME:-}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

LANDING_NGINX_DEPLOY_ENABLED="${LANDING_NGINX_DEPLOY_ENABLED:-}"
LANDING_NGINX_MAIN_ONLY="${LANDING_NGINX_MAIN_ONLY:-true}"
LANDING_NGINX_MAIN_BRANCH="${LANDING_NGINX_MAIN_BRANCH:-main}"
LANDING_NGINX_DOCROOT="${LANDING_NGINX_DOCROOT:-}"
LANDING_NGINX_RELOAD_CMD="${LANDING_NGINX_RELOAD_CMD:-}"
LANDING_NGINX_SYNC_MODE="${LANDING_NGINX_SYNC_MODE:-auto}"

is_true() {
  local v
  v="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "on" ]]
}

if ! is_true "$LANDING_NGINX_DEPLOY_ENABLED"; then
  echo "[landing-nginx] skip: LANDING_NGINX_DEPLOY_ENABLED is not true"
  exit 0
fi

if [[ -z "$BRANCH" ]]; then
  echo "[landing-nginx] skip: branch is empty"
  exit 0
fi

if is_true "$LANDING_NGINX_MAIN_ONLY" && [[ "$BRANCH" != "$LANDING_NGINX_MAIN_BRANCH" ]]; then
  echo "[landing-nginx] skip: branch=$BRANCH, allowed only $LANDING_NGINX_MAIN_BRANCH"
  exit 0
fi

if [[ -z "$LANDING_NGINX_DOCROOT" ]]; then
  echo "[landing-nginx] LANDING_NGINX_DOCROOT is required when deploy is enabled"
  exit 1
fi

SRC_DIR="$REPO_DIR/landing"
DST_DIR="$LANDING_NGINX_DOCROOT"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "[landing-nginx] source dir not found: $SRC_DIR"
  exit 1
fi
if [[ ! -f "$SRC_DIR/index.html" ]]; then
  echo "[landing-nginx] source index missing: $SRC_DIR/index.html"
  exit 1
fi

sync_local() {
  mkdir -p "$DST_DIR"
  rsync -a --delete \
    --exclude '*.rar' \
    --exclude 'tmp/' \
    "$SRC_DIR/" "$DST_DIR/"
  find "$DST_DIR" -type d -exec chmod 755 {} +
  find "$DST_DIR" -type f -exec chmod 644 {} +
}

sync_docker_host() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "[landing-nginx] docker is required for sync mode=docker-host"
    exit 1
  fi
  if [[ ! -S /var/run/docker.sock ]]; then
    echo "[landing-nginx] docker socket not found: /var/run/docker.sock"
    exit 1
  fi

  if ! command -v tar >/dev/null 2>&1; then
    echo "[landing-nginx] tar is required for sync mode=docker-host"
    exit 1
  fi

  # The runner itself may be containerized. In that case SRC_DIR exists only
  # inside the runner container and cannot be bind-mounted by the host daemon.
  # Stream the source archive over stdin while binding only the host docroot.
  tar \
    --exclude='*.rar' \
    --exclude='./tmp' \
    --exclude='./tmp/**' \
    -C "$SRC_DIR" \
    -cf - . | docker run --rm -i \
    -v "$DST_DIR:/dst" \
    alpine:3.20 sh -euc '
      find /dst -mindepth 1 -maxdepth 1 -exec rm -rf {} +
      tar -xf - -C /dst
      find /dst -type d -exec chmod 755 {} +
      find /dst -type f -exec chmod 644 {} +
    '
}

SYNC_MODE="$LANDING_NGINX_SYNC_MODE"
if [[ "$SYNC_MODE" == "auto" ]]; then
  if command -v docker >/dev/null 2>&1 && [[ -S /var/run/docker.sock ]]; then
    SYNC_MODE="docker-host"
  else
    SYNC_MODE="local"
  fi
fi

echo "[landing-nginx] sync mode=$SYNC_MODE branch=$BRANCH src=$SRC_DIR dst=$DST_DIR"
case "$SYNC_MODE" in
  local)
    sync_local
    ;;
  docker-host)
    sync_docker_host
    ;;
  *)
    echo "[landing-nginx] unknown LANDING_NGINX_SYNC_MODE=$SYNC_MODE (allowed: auto|local|docker-host)"
    exit 1
    ;;
esac

if [[ -n "$LANDING_NGINX_RELOAD_CMD" ]]; then
  echo "[landing-nginx] reload nginx via: $LANDING_NGINX_RELOAD_CMD"
  eval "$LANDING_NGINX_RELOAD_CMD"
fi

required_markers=(
  'https://www.instagram.com/impulsemarketing/'
  'href="terms.html"'
  'js/i18n.js?v=20260729-i18n1'
)

if [[ "$SYNC_MODE" == "docker-host" ]]; then
  docker run --rm \
    -v "$DST_DIR:/dst:ro" \
    alpine:3.20 sh -euc '
      index=/dst/index.html
      if [[ ! -f "$index" ]]; then
        echo "[landing-nginx] deployed index missing: $index"
        exit 1
      fi
      for marker in "$@"; do
        if ! grep -Fq "$marker" "$index"; then
          echo "[landing-nginx] marker missing in deployed index.html: $marker"
          exit 1
        fi
      done
    ' sh "${required_markers[@]}"
else
  if [[ ! -f "$DST_DIR/index.html" ]]; then
    echo "[landing-nginx] deployed index missing: $DST_DIR/index.html"
    exit 1
  fi
  for marker in "${required_markers[@]}"; do
    if ! grep -Fq "$marker" "$DST_DIR/index.html"; then
      echo "[landing-nginx] marker missing in deployed index.html: $marker"
      exit 1
    fi
  done
fi

echo "[landing-nginx] markers OK in deployed index.html"

echo "[landing-nginx] done"
