#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-${GITHUB_REF_NAME:-}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

ORCHESTRATOR_NGINX_DEPLOY_ENABLED="${ORCHESTRATOR_NGINX_DEPLOY_ENABLED:-}"
ORCHESTRATOR_NGINX_MAIN_ONLY="${ORCHESTRATOR_NGINX_MAIN_ONLY:-true}"
ORCHESTRATOR_NGINX_MAIN_BRANCH="${ORCHESTRATOR_NGINX_MAIN_BRANCH:-main}"

ORCHESTRATOR_NGINX_UPSTREAM_TEMPLATE="${ORCHESTRATOR_NGINX_UPSTREAM_TEMPLATE:-$REPO_DIR/infra/runners/nginx/orchestrator.upstream.conf.example}"
ORCHESTRATOR_NGINX_LOCATIONS_TEMPLATE="${ORCHESTRATOR_NGINX_LOCATIONS_TEMPLATE:-$REPO_DIR/infra/runners/nginx/orchestrator.locations.conf.example}"
ORCHESTRATOR_NGINX_UPSTREAM_TARGET="${ORCHESTRATOR_NGINX_UPSTREAM_TARGET:-}"
ORCHESTRATOR_NGINX_LOCATIONS_TARGET="${ORCHESTRATOR_NGINX_LOCATIONS_TARGET:-}"
ORCHESTRATOR_NGINX_RELOAD_CMD="${ORCHESTRATOR_NGINX_RELOAD_CMD:-}"

is_true() {
  local v
  v="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "on" ]]
}

if ! is_true "$ORCHESTRATOR_NGINX_DEPLOY_ENABLED"; then
  echo "[orch-nginx] skip: ORCHESTRATOR_NGINX_DEPLOY_ENABLED is not true"
  exit 0
fi

if [[ -z "$BRANCH" ]]; then
  echo "[orch-nginx] skip: branch is empty"
  exit 0
fi

if is_true "$ORCHESTRATOR_NGINX_MAIN_ONLY" && [[ "$BRANCH" != "$ORCHESTRATOR_NGINX_MAIN_BRANCH" ]]; then
  echo "[orch-nginx] skip: branch=$BRANCH, allowed only $ORCHESTRATOR_NGINX_MAIN_BRANCH"
  exit 0
fi

if [[ -z "$ORCHESTRATOR_NGINX_UPSTREAM_TARGET" ]]; then
  echo "[orch-nginx] ORCHESTRATOR_NGINX_UPSTREAM_TARGET is required when deploy is enabled"
  exit 1
fi
if [[ -z "$ORCHESTRATOR_NGINX_LOCATIONS_TARGET" ]]; then
  echo "[orch-nginx] ORCHESTRATOR_NGINX_LOCATIONS_TARGET is required when deploy is enabled"
  exit 1
fi

if [[ ! -f "$ORCHESTRATOR_NGINX_UPSTREAM_TEMPLATE" ]]; then
  echo "[orch-nginx] upstream template not found: $ORCHESTRATOR_NGINX_UPSTREAM_TEMPLATE"
  exit 1
fi
if [[ ! -f "$ORCHESTRATOR_NGINX_LOCATIONS_TEMPLATE" ]]; then
  echo "[orch-nginx] locations template not found: $ORCHESTRATOR_NGINX_LOCATIONS_TEMPLATE"
  exit 1
fi

echo "[orch-nginx] install upstream: $ORCHESTRATOR_NGINX_UPSTREAM_TEMPLATE -> $ORCHESTRATOR_NGINX_UPSTREAM_TARGET"
mkdir -p "$(dirname "$ORCHESTRATOR_NGINX_UPSTREAM_TARGET")"
install -m 0644 "$ORCHESTRATOR_NGINX_UPSTREAM_TEMPLATE" "$ORCHESTRATOR_NGINX_UPSTREAM_TARGET"

echo "[orch-nginx] install locations: $ORCHESTRATOR_NGINX_LOCATIONS_TEMPLATE -> $ORCHESTRATOR_NGINX_LOCATIONS_TARGET"
mkdir -p "$(dirname "$ORCHESTRATOR_NGINX_LOCATIONS_TARGET")"
install -m 0644 "$ORCHESTRATOR_NGINX_LOCATIONS_TEMPLATE" "$ORCHESTRATOR_NGINX_LOCATIONS_TARGET"

if ! grep -q "upstream blast_orchestrator_api_ha" "$ORCHESTRATOR_NGINX_UPSTREAM_TARGET"; then
  echo "[orch-nginx] validation failed: upstream marker missing in $ORCHESTRATOR_NGINX_UPSTREAM_TARGET"
  exit 1
fi
if ! grep -q "proxy_pass http://blast_orchestrator_api_ha;" "$ORCHESTRATOR_NGINX_LOCATIONS_TARGET"; then
  echo "[orch-nginx] validation failed: proxy_pass marker missing in $ORCHESTRATOR_NGINX_LOCATIONS_TARGET"
  exit 1
fi

if [[ -n "$ORCHESTRATOR_NGINX_RELOAD_CMD" ]]; then
  echo "[orch-nginx] reload nginx via: $ORCHESTRATOR_NGINX_RELOAD_CMD"
  eval "$ORCHESTRATOR_NGINX_RELOAD_CMD"
fi

echo "[orch-nginx] done"
