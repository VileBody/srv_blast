#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-${GITHUB_REF_NAME:-}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

BLAST_OPS_NGINX_DEPLOY_ENABLED="${BLAST_OPS_NGINX_DEPLOY_ENABLED:-}"
BLAST_OPS_NGINX_MAIN_ONLY="${BLAST_OPS_NGINX_MAIN_ONLY:-true}"
BLAST_OPS_NGINX_MAIN_BRANCH="${BLAST_OPS_NGINX_MAIN_BRANCH:-main}"

BLAST_OPS_NGINX_UPSTREAM_TEMPLATE="${BLAST_OPS_NGINX_UPSTREAM_TEMPLATE:-$REPO_DIR/infra/runners/nginx/blast-ops.upstream.conf.example}"
BLAST_OPS_NGINX_LOCATIONS_TEMPLATE="${BLAST_OPS_NGINX_LOCATIONS_TEMPLATE:-$REPO_DIR/infra/runners/nginx/blast-ops.locations.conf.example}"
BLAST_OPS_NGINX_UPSTREAM_TARGET="${BLAST_OPS_NGINX_UPSTREAM_TARGET:-}"
BLAST_OPS_NGINX_LOCATIONS_TARGET="${BLAST_OPS_NGINX_LOCATIONS_TARGET:-}"
BLAST_OPS_NGINX_RELOAD_CMD="${BLAST_OPS_NGINX_RELOAD_CMD:-}"

BLAST_OPS_ORCH0_API_UPSTREAM="${BLAST_OPS_ORCH0_API_UPSTREAM:-}"
BLAST_OPS_ORCH1_API_UPSTREAM="${BLAST_OPS_ORCH1_API_UPSTREAM:-}"
BLAST_OPS_ORCH0_TG_WEBHOOK_UPSTREAM="${BLAST_OPS_ORCH0_TG_WEBHOOK_UPSTREAM:-}"
BLAST_OPS_ORCH1_TG_WEBHOOK_UPSTREAM="${BLAST_OPS_ORCH1_TG_WEBHOOK_UPSTREAM:-}"
BLAST_OPS_TG_WEBHOOK_PATH="${BLAST_OPS_TG_WEBHOOK_PATH:-/telegram/webhook}"

is_true() {
  local v
  v="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "on" ]]
}

run_as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
    return
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo "$@"
    return
  fi
  echo "[blast-ops-nginx] root privileges required (run as root or via sudo)"
  return 1
}

if ! is_true "$BLAST_OPS_NGINX_DEPLOY_ENABLED"; then
  echo "[blast-ops-nginx] skip: BLAST_OPS_NGINX_DEPLOY_ENABLED is not true"
  exit 0
fi

if [[ -z "$BRANCH" ]]; then
  echo "[blast-ops-nginx] skip: branch is empty"
  exit 0
fi

if is_true "$BLAST_OPS_NGINX_MAIN_ONLY" && [[ "$BRANCH" != "$BLAST_OPS_NGINX_MAIN_BRANCH" ]]; then
  echo "[blast-ops-nginx] skip: branch=$BRANCH, allowed only $BLAST_OPS_NGINX_MAIN_BRANCH"
  exit 0
fi

if [[ -z "$BLAST_OPS_NGINX_UPSTREAM_TARGET" || -z "$BLAST_OPS_NGINX_LOCATIONS_TARGET" ]]; then
  echo "[blast-ops-nginx] BLAST_OPS_NGINX_UPSTREAM_TARGET and BLAST_OPS_NGINX_LOCATIONS_TARGET are required"
  exit 1
fi

if [[ ! -f "$BLAST_OPS_NGINX_UPSTREAM_TEMPLATE" ]]; then
  echo "[blast-ops-nginx] upstream template not found: $BLAST_OPS_NGINX_UPSTREAM_TEMPLATE"
  exit 1
fi
if [[ ! -f "$BLAST_OPS_NGINX_LOCATIONS_TEMPLATE" ]]; then
  echo "[blast-ops-nginx] locations template not found: $BLAST_OPS_NGINX_LOCATIONS_TEMPLATE"
  exit 1
fi

if [[ -z "$BLAST_OPS_ORCH0_API_UPSTREAM" || -z "$BLAST_OPS_ORCH1_API_UPSTREAM" ]]; then
  echo "[blast-ops-nginx] ORCH API upstream vars are required"
  exit 1
fi
if [[ -z "$BLAST_OPS_ORCH0_TG_WEBHOOK_UPSTREAM" || -z "$BLAST_OPS_ORCH1_TG_WEBHOOK_UPSTREAM" ]]; then
  echo "[blast-ops-nginx] TG webhook upstream vars are required"
  exit 1
fi

WEBHOOK_PATH="${BLAST_OPS_TG_WEBHOOK_PATH:-/telegram/webhook}"
if [[ -z "$WEBHOOK_PATH" ]]; then
  echo "[blast-ops-nginx] BLAST_OPS_TG_WEBHOOK_PATH must be non-empty"
  exit 1
fi
if [[ "${WEBHOOK_PATH:0:1}" != "/" ]]; then
  WEBHOOK_PATH="/$WEBHOOK_PATH"
fi

escape_sed_replacement() {
  printf '%s' "$1" | sed -e 's/[\/&]/\\&/g'
}

tmpl_upstream="$(cat "$BLAST_OPS_NGINX_UPSTREAM_TEMPLATE")"
tmpl_locations="$(cat "$BLAST_OPS_NGINX_LOCATIONS_TEMPLATE")"

orch0_api_escaped="$(escape_sed_replacement "$BLAST_OPS_ORCH0_API_UPSTREAM")"
orch1_api_escaped="$(escape_sed_replacement "$BLAST_OPS_ORCH1_API_UPSTREAM")"
orch0_tg_escaped="$(escape_sed_replacement "$BLAST_OPS_ORCH0_TG_WEBHOOK_UPSTREAM")"
orch1_tg_escaped="$(escape_sed_replacement "$BLAST_OPS_ORCH1_TG_WEBHOOK_UPSTREAM")"
webhook_path_escaped="$(escape_sed_replacement "$WEBHOOK_PATH")"

tmp_dir="$(mktemp -d)"
upstream_rendered="$tmp_dir/blast-ops.upstream.conf"
locations_rendered="$tmp_dir/blast-ops.locations.conf"

printf '%s\n' "$tmpl_upstream" \
  | sed \
      -e "s/__ORCH0_API_UPSTREAM__/$orch0_api_escaped/g" \
      -e "s/__ORCH1_API_UPSTREAM__/$orch1_api_escaped/g" \
      -e "s/__ORCH0_TG_WEBHOOK_UPSTREAM__/$orch0_tg_escaped/g" \
      -e "s/__ORCH1_TG_WEBHOOK_UPSTREAM__/$orch1_tg_escaped/g" \
  > "$upstream_rendered"

printf '%s\n' "$tmpl_locations" \
  | sed -e "s/__TG_WEBHOOK_PATH__/$webhook_path_escaped/g" \
  > "$locations_rendered"

if grep -q "__ORCH0_API_UPSTREAM__\\|__ORCH1_API_UPSTREAM__\\|__ORCH0_TG_WEBHOOK_UPSTREAM__\\|__ORCH1_TG_WEBHOOK_UPSTREAM__" "$upstream_rendered"; then
  echo "[blast-ops-nginx] unresolved placeholders in rendered upstream config"
  exit 1
fi
if grep -q "__TG_WEBHOOK_PATH__" "$locations_rendered"; then
  echo "[blast-ops-nginx] unresolved placeholders in rendered locations config"
  exit 1
fi

echo "[blast-ops-nginx] install upstream -> $BLAST_OPS_NGINX_UPSTREAM_TARGET"
run_as_root mkdir -p "$(dirname "$BLAST_OPS_NGINX_UPSTREAM_TARGET")"
run_as_root install -m 0644 "$upstream_rendered" "$BLAST_OPS_NGINX_UPSTREAM_TARGET"

echo "[blast-ops-nginx] install locations -> $BLAST_OPS_NGINX_LOCATIONS_TARGET"
run_as_root mkdir -p "$(dirname "$BLAST_OPS_NGINX_LOCATIONS_TARGET")"
run_as_root install -m 0644 "$locations_rendered" "$BLAST_OPS_NGINX_LOCATIONS_TARGET"

if [[ -n "$BLAST_OPS_NGINX_RELOAD_CMD" ]]; then
  echo "[blast-ops-nginx] reload nginx via: $BLAST_OPS_NGINX_RELOAD_CMD"
  eval "$BLAST_OPS_NGINX_RELOAD_CMD"
fi

rm -rf "$tmp_dir"
echo "[blast-ops-nginx] done"
