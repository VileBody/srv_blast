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
ORCHESTRATOR_NGINX_RELOAD_CMD="${ORCHESTRATOR_NGINX_RELOAD_CMD:-sudo nginx -t && sudo systemctl reload nginx}"

ORCHESTRATOR_NGINX_REMOTE_HOST="${ORCHESTRATOR_NGINX_REMOTE_HOST:-}"
ORCHESTRATOR_NGINX_REMOTE_USER="${ORCHESTRATOR_NGINX_REMOTE_USER:-blast}"
ORCHESTRATOR_NGINX_REMOTE_PORT="${ORCHESTRATOR_NGINX_REMOTE_PORT:-22}"
ORCHESTRATOR_NGINX_REMOTE_SSH_KEY_PATH="${ORCHESTRATOR_NGINX_REMOTE_SSH_KEY_PATH:-}"

is_true() {
  local v
  v="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "on" ]]
}

if ! is_true "$ORCHESTRATOR_NGINX_DEPLOY_ENABLED"; then
  echo "[orch-nginx-remote] skip: ORCHESTRATOR_NGINX_DEPLOY_ENABLED is not true"
  exit 0
fi

if [[ -z "$BRANCH" ]]; then
  echo "[orch-nginx-remote] skip: branch is empty"
  exit 0
fi

if is_true "$ORCHESTRATOR_NGINX_MAIN_ONLY" && [[ "$BRANCH" != "$ORCHESTRATOR_NGINX_MAIN_BRANCH" ]]; then
  echo "[orch-nginx-remote] skip: branch=$BRANCH, allowed only $ORCHESTRATOR_NGINX_MAIN_BRANCH"
  exit 0
fi

if [[ -z "$ORCHESTRATOR_NGINX_REMOTE_HOST" ]]; then
  echo "[orch-nginx-remote] ORCHESTRATOR_NGINX_REMOTE_HOST is required"
  exit 1
fi
if [[ -z "$ORCHESTRATOR_NGINX_REMOTE_SSH_KEY_PATH" ]]; then
  echo "[orch-nginx-remote] ORCHESTRATOR_NGINX_REMOTE_SSH_KEY_PATH is required"
  exit 1
fi
if [[ ! -f "$ORCHESTRATOR_NGINX_REMOTE_SSH_KEY_PATH" ]]; then
  echo "[orch-nginx-remote] SSH key file not found: $ORCHESTRATOR_NGINX_REMOTE_SSH_KEY_PATH"
  exit 1
fi
if [[ -z "$ORCHESTRATOR_NGINX_UPSTREAM_TARGET" ]]; then
  echo "[orch-nginx-remote] ORCHESTRATOR_NGINX_UPSTREAM_TARGET is required"
  exit 1
fi
if [[ -z "$ORCHESTRATOR_NGINX_LOCATIONS_TARGET" ]]; then
  echo "[orch-nginx-remote] ORCHESTRATOR_NGINX_LOCATIONS_TARGET is required"
  exit 1
fi
if [[ ! -f "$ORCHESTRATOR_NGINX_UPSTREAM_TEMPLATE" ]]; then
  echo "[orch-nginx-remote] upstream template not found: $ORCHESTRATOR_NGINX_UPSTREAM_TEMPLATE"
  exit 1
fi
if [[ ! -f "$ORCHESTRATOR_NGINX_LOCATIONS_TEMPLATE" ]]; then
  echo "[orch-nginx-remote] locations template not found: $ORCHESTRATOR_NGINX_LOCATIONS_TEMPLATE"
  exit 1
fi

if ! grep -q "upstream blast_orchestrator_api_ha" "$ORCHESTRATOR_NGINX_UPSTREAM_TEMPLATE"; then
  echo "[orch-nginx-remote] validation failed: upstream marker missing in $ORCHESTRATOR_NGINX_UPSTREAM_TEMPLATE"
  exit 1
fi
if ! grep -q "proxy_pass http://blast_orchestrator_api_ha;" "$ORCHESTRATOR_NGINX_LOCATIONS_TEMPLATE"; then
  echo "[orch-nginx-remote] validation failed: proxy_pass marker missing in $ORCHESTRATOR_NGINX_LOCATIONS_TEMPLATE"
  exit 1
fi

target="${ORCHESTRATOR_NGINX_REMOTE_USER}@${ORCHESTRATOR_NGINX_REMOTE_HOST}"
tmp_upstream="/tmp/blast_orchestrator_upstream.${RANDOM}.$$.conf"
tmp_locations="/tmp/blast_orchestrator_locations.${RANDOM}.$$.conf"

ssh_opts=(
  -p "$ORCHESTRATOR_NGINX_REMOTE_PORT"
  -i "$ORCHESTRATOR_NGINX_REMOTE_SSH_KEY_PATH"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=20
)
scp_opts=(
  -P "$ORCHESTRATOR_NGINX_REMOTE_PORT"
  -i "$ORCHESTRATOR_NGINX_REMOTE_SSH_KEY_PATH"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=20
)

printf -v q_tmp_upstream '%q' "$tmp_upstream"
printf -v q_tmp_locations '%q' "$tmp_locations"
printf -v q_upstream_target '%q' "$ORCHESTRATOR_NGINX_UPSTREAM_TARGET"
printf -v q_locations_target '%q' "$ORCHESTRATOR_NGINX_LOCATIONS_TARGET"
printf -v q_upstream_dir '%q' "$(dirname "$ORCHESTRATOR_NGINX_UPSTREAM_TARGET")"
printf -v q_locations_dir '%q' "$(dirname "$ORCHESTRATOR_NGINX_LOCATIONS_TARGET")"
printf -v q_reload_cmd '%q' "$ORCHESTRATOR_NGINX_RELOAD_CMD"

echo "[orch-nginx-remote] upload templates to $target"
scp "${scp_opts[@]}" "$ORCHESTRATOR_NGINX_UPSTREAM_TEMPLATE" "$target:$tmp_upstream"
scp "${scp_opts[@]}" "$ORCHESTRATOR_NGINX_LOCATIONS_TEMPLATE" "$target:$tmp_locations"

remote_cmd=$(
  cat <<EOF
set -euo pipefail
cleanup() {
  rm -f $q_tmp_upstream $q_tmp_locations
}
trap cleanup EXIT

sudo mkdir -p $q_upstream_dir $q_locations_dir
sudo install -m 0644 $q_tmp_upstream $q_upstream_target
sudo install -m 0644 $q_tmp_locations $q_locations_target

grep -q 'upstream blast_orchestrator_api_ha' $q_upstream_target
grep -q 'proxy_pass http://blast_orchestrator_api_ha;' $q_locations_target

if [[ -n $q_reload_cmd ]]; then
  eval $q_reload_cmd
fi
EOF
)
printf -v q_remote_cmd '%q' "$remote_cmd"

echo "[orch-nginx-remote] install and reload on $target"
ssh "${ssh_opts[@]}" "$target" "bash -lc $q_remote_cmd"

echo "[orch-nginx-remote] done"
