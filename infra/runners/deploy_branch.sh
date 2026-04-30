#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-${GITHUB_REF_NAME:-}}"
DEPLOY_STACK="${2:-${DEPLOY_STACK:-all}}"
DEPLOY_PRUNE_OTHER_STACK="${DEPLOY_PRUNE_OTHER_STACK:-false}"
DEPLOY_ORCHESTRATOR_HA="${DEPLOY_ORCHESTRATOR_HA:-false}"
DEPLOY_ORCHESTRATOR_HA_COMPOSE_FILE="${DEPLOY_ORCHESTRATOR_HA_COMPOSE_FILE:-docker-compose.orchestrator-ha.yml}"
PROD_TG_WEBHOOK_IP_ADDRESS="${PROD_TG_WEBHOOK_IP_ADDRESS:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
RUNNERS_DIR="$REPO_DIR/infra/runners"

is_true() {
  local v
  v="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "on" ]]
}

env_file_value() {
  local key="$1"
  local env_file="$REPO_DIR/.env"
  if [[ ! -f "$env_file" ]]; then
    return 0
  fi
  grep -E "^${key}=" "$env_file" | tail -n1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' || true
}

generic_env_file_value() {
  local env_file="$1"
  local key="$2"
  if [[ ! -f "$env_file" ]]; then
    return 0
  fi
  grep -E "^${key}=" "$env_file" | tail -n1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' || true
}

require_env_file_value() {
  local env_file="$1"
  local key="$2"
  local current
  current="$(generic_env_file_value "$env_file" "$key")"
  if [[ -z "$current" ]]; then
    echo "[deploy] missing required $key in $env_file"
    return 1
  fi
  return 0
}

set_env_file_value() {
  local env_file="$1"
  local key="$2"
  local value="$3"
  local tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { done = 0 }
    $0 ~ "^[[:space:]]*" key "=" {
      if (!done) {
        print key "=" value
        done = 1
      }
      next
    }
    { print }
    END {
      if (!done) {
        print key "=" value
      }
    }
  ' "$env_file" > "$tmp"
  mv "$tmp" "$env_file"
}

ensure_env_file_value() {
  local env_file="$1"
  local key="$2"
  local value="$3"
  local current
  current="$(generic_env_file_value "$env_file" "$key")"
  if [[ -n "$current" ]]; then
    return 0
  fi
  set_env_file_value "$env_file" "$key" "$value"
  echo "[deploy] set $key in $env_file for legacy Dozzle env compatibility"
}

dozzle_remote_agent_default() {
  printf '%s' "${DOZZLE_REMOTE_AGENT_DEFAULT:-192.168.0.8:7007,192.168.0.11:7007}"
}

is_remote_reachable_bind_host() {
  local host="$1"
  [[ -n "$host" && "$host" != "127."* && "$host" != "localhost" && "$host" != "0.0.0.0" ]]
}

detect_private_bind_host() {
  local explicit first_172 iface cidr host
  explicit="$(env_file_value DOZZLE_AGENT_BIND_HOST)"
  if is_remote_reachable_bind_host "$explicit"; then
    printf '%s\n' "$explicit"
    return 0
  fi
  if ! command -v ip >/dev/null 2>&1; then
    return 1
  fi
  first_172=""
  while read -r iface cidr; do
    host="${cidr%%/*}"
    case "$iface" in
      docker*|br-*|veth*|lo)
        continue
        ;;
    esac
    case "$host" in
      192.168.*|10.*)
        printf '%s\n' "$host"
        return 0
        ;;
      172.1[6-9].*|172.2[0-9].*|172.3[0-1].*)
        if [[ -z "$first_172" ]]; then
          first_172="$host"
        fi
        ;;
    esac
  done < <(ip -o -4 addr show scope global | awk '{print $2, $4}')
  if [[ -n "$first_172" ]]; then
    printf '%s\n' "$first_172"
    return 0
  fi
  return 1
}

dozzle_agent_hostname_default() {
  local configured host
  configured="$(env_file_value ORCHESTRATOR_NODE_NAME)"
  if [[ -n "$configured" ]]; then
    printf '%s\n' "$configured"
    return 0
  fi
  host="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
  if [[ -n "$host" ]]; then
    printf '%s\n' "$host"
    return 0
  fi
  return 1
}

bootstrap_dozzle_agent_env() {
  local env_file="$RUNNERS_DIR/.env.dozzle-agent"
  local bind_host agent_hostname

  if [[ ! -f "$env_file" ]]; then
    install -m 600 /dev/null "$env_file"
    echo "[deploy] created Dozzle agent env file: $env_file"
  fi

  bind_host="$(generic_env_file_value "$env_file" DOZZLE_AGENT_BIND_HOST)"
  if [[ -z "$bind_host" ]]; then
    bind_host="$(detect_private_bind_host || true)"
    if [[ -z "$bind_host" ]]; then
      echo "[deploy] cannot infer DOZZLE_AGENT_BIND_HOST for $env_file; set it to this node private IP"
      return 2
    fi
    ensure_env_file_value "$env_file" DOZZLE_AGENT_BIND_HOST "$bind_host"
  fi

  agent_hostname="$(generic_env_file_value "$env_file" DOZZLE_AGENT_HOSTNAME)"
  if [[ -z "$agent_hostname" ]]; then
    agent_hostname="$(dozzle_agent_hostname_default || true)"
    if [[ -z "$agent_hostname" ]]; then
      echo "[deploy] cannot infer DOZZLE_AGENT_HOSTNAME for $env_file"
      return 2
    fi
    ensure_env_file_value "$env_file" DOZZLE_AGENT_HOSTNAME "$agent_hostname"
  fi

  ensure_env_file_value "$env_file" DOZZLE_AGENT_PORT "7007"
  ensure_env_file_value "$env_file" DOZZLE_AGENT_LEVEL "info"
}

infra_app_services() {
  printf '%s\n' tg-bot tg-bot-public-admin asset-ui finance-bot
}

bootstrap_infra_orchestrator_url() {
  local value
  value="${INFRA_ORCHESTRATOR_PUBLIC_URL:-}"
  if [[ -z "$value" ]]; then
    return 0
  fi
  set_env_file_value "$REPO_DIR/.env" ORCHESTRATOR_PUBLIC_URL "$value"
  echo "[deploy] set ORCHESTRATOR_PUBLIC_URL for infra stack"
}

require_infra_ops_public_admin_env() {
  local orchestrator_url
  orchestrator_url="$(env_file_value ORCHESTRATOR_PUBLIC_URL)"
  if [[ -z "$orchestrator_url" ]]; then
    echo "[deploy] ORCHESTRATOR_PUBLIC_URL is required for tg-bot-public-admin on infra stack"
    echo "[deploy] set it to the orchestrator load balancer/public endpoint, not a local compose service"
    return 1
  fi
  case "$orchestrator_url" in
    http://orchestrator-api:*|http://127.*|https://127.*|http://localhost*|https://localhost*)
      echo "[deploy] invalid ORCHESTRATOR_PUBLIC_URL for infra stack: $orchestrator_url"
      echo "[deploy] blast_ops must point tg-bot-public-admin to the orchestrator load balancer/public endpoint"
      return 1
      ;;
  esac
}

bootstrap_tg_webhook_ip_env() {
  local mode override current url host ip
  mode="$(printf '%s' "$(env_file_value TG_DELIVERY_MODE)" | tr '[:upper:]' '[:lower:]')"
  if [[ "$mode" != "webhook" ]]; then
    return 0
  fi

  override="$(printf '%s' "$PROD_TG_WEBHOOK_IP_ADDRESS" | tr -d '[:space:]')"
  if [[ -n "$override" ]]; then
    set_env_file_value "$REPO_DIR/.env" TG_WEBHOOK_IP_ADDRESS "$override"
    echo "[deploy] set TG_WEBHOOK_IP_ADDRESS=$override from PROD_TG_WEBHOOK_IP_ADDRESS"
    return 0
  fi

  current="$(env_file_value TG_WEBHOOK_IP_ADDRESS)"
  if [[ -n "$current" ]]; then
    return 0
  fi

  url="$(env_file_value TG_WEBHOOK_URL)"
  if [[ -z "$url" ]]; then
    return 0
  fi

  host="$(python3 - "$url" <<'PY'
from urllib.parse import urlparse
import sys

parsed = urlparse(sys.argv[1])
print(parsed.hostname or "")
PY
)"
  if [[ -z "$host" ]]; then
    echo "[deploy] cannot parse TG_WEBHOOK_URL host: $url"
    return 1
  fi
  ip="$(getent ahostsv4 "$host" | awk '{ print $1; exit }')"
  if [[ -z "$ip" ]]; then
    echo "[deploy] cannot resolve IPv4 for TG_WEBHOOK_URL host: $host"
    return 1
  fi

  set_env_file_value "$REPO_DIR/.env" TG_WEBHOOK_IP_ADDRESS "$ip"
  echo "[deploy] set TG_WEBHOOK_IP_ADDRESS=$ip for Telegram webhook DNS pin"
}

if [[ -z "$BRANCH" ]]; then
  echo "Branch is not specified. Pass it as the first argument."
  exit 1
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "REPO_DIR is not a git repository: $REPO_DIR"
  exit 1
fi

cd "$REPO_DIR"

echo "[deploy] repo=$REPO_DIR branch=$BRANCH stack=$DEPLOY_STACK"

# Prefer an explicit PAT secret, then fallback to the workflow token.
AUTH_TOKEN="${GIT_AUTH_TOKEN:-${GITHUB_TOKEN_FALLBACK:-}}"

# Self-hosted runner containers may run under a different UID than the
# mounted repository owner. Mark repo as safe to avoid "dubious ownership".
if ! git config --global --get-all safe.directory 2>/dev/null | grep -Fxq "$REPO_DIR"; then
  git config --global --add safe.directory "$REPO_DIR"
fi

REMOTE_URL="$(git remote get-url origin)"
case "$REMOTE_URL" in
  git@github.com:*|ssh://git@github.com/*|https://github.com/*)
    if [[ -z "$AUTH_TOKEN" ]]; then
      echo "GitHub token is missing for non-interactive deploy."
      echo "Set repository secret DEPLOY_GH_TOKEN or ensure github.token is available."
      exit 1
    fi
    ;;
esac

git_run() {
  if [[ -n "$AUTH_TOKEN" ]]; then
    git \
      -c "url.https://x-access-token:${AUTH_TOKEN}@github.com/.insteadof=git@github.com:" \
      -c "url.https://x-access-token:${AUTH_TOKEN}@github.com/.insteadof=ssh://git@github.com/" \
      -c "url.https://x-access-token:${AUTH_TOKEN}@github.com/.insteadof=https://github.com/" \
      "$@"
  else
    git "$@"
  fi
}

# In older branch states this runtime-generated file was tracked and may be
# modified by running services. Normalize it before checkout/pull so deploy can
# fast-forward cleanly without broad auto-stash.
RUNTIME_TRACKED_FILE="data/footage_inventory_selected.json"
if git ls-files --error-unmatch "$RUNTIME_TRACKED_FILE" >/dev/null 2>&1; then
  if ! git diff --quiet -- "$RUNTIME_TRACKED_FILE" || ! git diff --cached --quiet -- "$RUNTIME_TRACKED_FILE"; then
    echo "[deploy] reset modified runtime file: $RUNTIME_TRACKED_FILE"
    git_run restore --staged --worktree -- "$RUNTIME_TRACKED_FILE"
  fi
fi

git_run fetch origin "$BRANCH"

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git_run checkout -f "$BRANCH"
else
  git_run checkout -B "$BRANCH" "origin/$BRANCH"
fi

# Deterministic deploy target: exact remote branch revision.
git_run reset --hard "origin/$BRANCH"

deploy_root_services() {
  local services=("$@")
  if [[ ${#services[@]} -eq 0 ]]; then
    echo "[deploy] docker compose up -d --build"
    docker compose up -d --build
  else
    echo "[deploy] docker compose up -d --build ${services[*]}"
    docker compose up -d --build "${services[@]}"
  fi
}

deploy_prod_path_services() {
  if ! is_true "$DEPLOY_ORCHESTRATOR_HA"; then
    deploy_root_services orchestrator-api worker-build worker-render worker-render-poll tg-bot-public
    return 0
  fi

  local compose_ha="$DEPLOY_ORCHESTRATOR_HA_COMPOSE_FILE"
  if [[ -z "$compose_ha" ]]; then
    echo "[deploy] DEPLOY_ORCHESTRATOR_HA=true but DEPLOY_ORCHESTRATOR_HA_COMPOSE_FILE is empty"
    exit 1
  fi
  if [[ "$compose_ha" != /* ]]; then
    compose_ha="$REPO_DIR/$compose_ha"
  fi
  if [[ ! -f "$compose_ha" ]]; then
    echo "[deploy] DEPLOY_ORCHESTRATOR_HA=true but compose override not found: $compose_ha"
    exit 1
  fi

  echo "[deploy] orchestrator-ha enabled compose=$compose_ha"
  echo "[deploy] docker compose -f docker-compose.yml -f $compose_ha up -d --build orchestrator-api orchestrator-api-2 worker-build worker-render worker-render-poll tg-bot-public"
  docker compose -f docker-compose.yml -f "$compose_ha" up -d --build \
    orchestrator-api orchestrator-api-2 worker-build worker-render worker-render-poll tg-bot-public
}

stop_root_services() {
  local services=("$@")
  if [[ ${#services[@]} -eq 0 ]]; then
    return 0
  fi
  echo "[deploy] docker compose stop ${services[*]}"
  docker compose stop "${services[@]}" || true
}

remove_root_services() {
  local services=("$@")
  if [[ ${#services[@]} -eq 0 ]]; then
    return 0
  fi
  stop_root_services "${services[@]}"
  echo "[deploy] docker compose rm -f ${services[*]}"
  docker compose rm -f "${services[@]}" || true
}

deploy_runner_compose_if_present() {
  local compose_file="$1"
  local env_file="$2"
  if [[ ! -f "$compose_file" ]]; then
    echo "[deploy] skip missing compose file: $compose_file"
    return 0
  fi
  if [[ ! -f "$env_file" ]]; then
    echo "[deploy] skip $compose_file (env file not found: $env_file)"
    return 0
  fi
  echo "[deploy] docker compose -f $compose_file --env-file $env_file up -d"
  docker compose -f "$compose_file" --env-file "$env_file" up -d
}

dozzle_central_env_is_ready() {
  local env_file="$RUNNERS_DIR/.env.dozzle"
  local bind_host missing_required=0

  if [[ ! -f "$env_file" ]]; then
    echo "[deploy] skip Dozzle central deploy (env file not found: $env_file)"
    return 1
  fi

  ensure_env_file_value "$env_file" DOZZLE_AUTH_PROVIDER "none"
  ensure_env_file_value "$env_file" DOZZLE_BASE "/logs"
  ensure_env_file_value "$env_file" DOZZLE_HOSTNAME "blast-ops"
  ensure_env_file_value "$env_file" DOZZLE_REMOTE_AGENT "$(dozzle_remote_agent_default)"

  require_env_file_value "$env_file" DOZZLE_BIND_HOST || missing_required=1
  require_env_file_value "$env_file" DOZZLE_PORT || missing_required=1
  require_env_file_value "$env_file" DOZZLE_AUTH_PROVIDER || missing_required=1
  require_env_file_value "$env_file" DOZZLE_BASE || missing_required=1
  require_env_file_value "$env_file" DOZZLE_HOSTNAME || missing_required=1
  if [[ "$missing_required" -ne 0 ]]; then
    return 2
  fi

  bind_host="$(generic_env_file_value "$env_file" DOZZLE_BIND_HOST)"
  if [[ "$bind_host" == "0.0.0.0" ]]; then
    echo "[deploy] invalid DOZZLE_BIND_HOST=0.0.0.0 in $env_file; bind central Dozzle to localhost/private IP and expose it through nginx auth"
    return 2
  fi

  return 0
}

dozzle_agent_env_is_ready() {
  local env_file="$RUNNERS_DIR/.env.dozzle-agent"
  local bind_host missing_required=0
  bootstrap_dozzle_agent_env || return $?

  require_env_file_value "$env_file" DOZZLE_AGENT_BIND_HOST || missing_required=1
  require_env_file_value "$env_file" DOZZLE_AGENT_PORT || missing_required=1
  require_env_file_value "$env_file" DOZZLE_AGENT_HOSTNAME || missing_required=1
  if [[ "$missing_required" -ne 0 ]]; then
    return 2
  fi

  bind_host="$(generic_env_file_value "$env_file" DOZZLE_AGENT_BIND_HOST)"
  if ! is_remote_reachable_bind_host "$bind_host"; then
    echo "[deploy] invalid DOZZLE_AGENT_BIND_HOST=${bind_host:-<empty>} in $env_file; set it to this node private IP"
    return 2
  fi
  return 0
}

deploy_github_runner_compose_if_allowed() {
  local compose_file="$1"
  local env_file="$2"
  if [[ "${GITHUB_ACTIONS:-}" == "true" ]] && ! is_true "${DEPLOY_SELF_RESTART_RUNNER:-false}"; then
    echo "[deploy] skip $compose_file during GitHub Actions job"
    echo "[deploy] set DEPLOY_SELF_RESTART_RUNNER=true to force self-restart"
    return 0
  fi
  deploy_runner_compose_if_present "$compose_file" "$env_file"
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
  echo "[deploy] logs systemd setup requires root privileges or sudo"
  return 1
}

can_manage_systemd() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return 1
  fi
  if ! systemctl show --property=Version >/dev/null 2>&1; then
    return 1
  fi
  return 0
}

DETECTED_LOGS_PYTHON=""

detect_logs_python() {
  local logs_python="/opt/blast-logs-venv/bin/python"
  local deps_probe='import boto3, httpx, asyncpg, docker  # noqa: F401'
  local logs_pip_pkgs=(boto3 httpx asyncpg docker)
  DETECTED_LOGS_PYTHON=""

  if [[ -x "$logs_python" ]]; then
    if ! "$logs_python" -c "$deps_probe" >/dev/null 2>&1; then
      echo "[deploy] install logs pipeline deps into $logs_python" >&2
      run_as_root "$logs_python" -m pip install --upgrade pip >&2
      run_as_root "$logs_python" -m pip install "${logs_pip_pkgs[@]}" >&2
    fi
    DETECTED_LOGS_PYTHON="$logs_python"
    return 0
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "[deploy] python3 is required for logs pipeline bootstrap" >&2
    return 1
  fi

  echo "[deploy] bootstrap logs venv at /opt/blast-logs-venv" >&2
  if ! run_as_root python3 -m venv /opt/blast-logs-venv; then
    echo "[deploy] failed to create /opt/blast-logs-venv (install python3-venv on host)" >&2
    return 1
  fi

  run_as_root /opt/blast-logs-venv/bin/pip install --upgrade pip >&2
  run_as_root /opt/blast-logs-venv/bin/pip install "${logs_pip_pkgs[@]}" >&2
  DETECTED_LOGS_PYTHON="$logs_python"
  return 0
}

deploy_logs_pipeline_systemd_if_present() {
  local env_file="$RUNNERS_DIR/.env.logs-backup"
  local units_dir="$REPO_DIR/infra/logging/systemd"
  local target_env_file="/etc/blast/logs-backup.env"

  if [[ ! -f "$env_file" ]]; then
    echo "[deploy] skip logs pipeline systemd setup (env missing: $env_file)"
    return 0
  fi
  if [[ ! -d "$units_dir" ]]; then
    echo "[deploy] skip logs pipeline systemd setup (units dir missing: $units_dir)"
    return 0
  fi

  local enabled_raw=""
  enabled_raw="$(grep -E '^LOG_BACKUP_ENABLED=' "$env_file" | tail -n1 | cut -d= -f2- | tr -d '"' | xargs || true)"
  local enabled_norm
  enabled_norm="$(printf '%s' "$enabled_raw" | tr '[:upper:]' '[:lower:]')"
  if [[ "$enabled_norm" != "1" && "$enabled_norm" != "true" && "$enabled_norm" != "yes" && "$enabled_norm" != "on" ]]; then
    echo "[deploy] skip logs pipeline systemd setup (LOG_BACKUP_ENABLED is not true)"
    return 0
  fi

  local mode_raw=""
  mode_raw="$(grep -E '^LOG_BACKUP_MODE=' "$env_file" | tail -n1 | cut -d= -f2- | tr -d '"' | xargs || true)"
  local mode_norm
  mode_norm="$(printf '%s' "${mode_raw:-centralized}" | tr '[:upper:]' '[:lower:]')"
  if [[ -z "$mode_norm" ]]; then
    mode_norm="centralized"
  fi
  if [[ "$mode_norm" != "centralized" && "$mode_norm" != "distributed" ]]; then
    echo "[deploy] invalid LOG_BACKUP_MODE=$mode_raw (expected centralized|distributed)"
    return 1
  fi

  local node_role_raw=""
  node_role_raw="$(grep -E '^LOG_BACKUP_NODE_ROLE=' "$env_file" | tail -n1 | cut -d= -f2- | tr -d '"' | xargs || true)"
  local node_role_norm
  node_role_norm="$(printf '%s' "$node_role_raw" | tr '[:upper:]' '[:lower:]')"
  if [[ "$mode_norm" == "centralized" && "$node_role_norm" != "logs-service" ]]; then
    echo "[deploy] centralized logs mode requires LOG_BACKUP_NODE_ROLE=logs-service (got: ${node_role_raw:-<empty>})"
    return 1
  fi

  local logs_python=""
  if ! detect_logs_python; then
    echo "[deploy] failed to detect logs pipeline python interpreter"
    return 1
  fi
  logs_python="$DETECTED_LOGS_PYTHON"
  if [[ -z "$logs_python" ]]; then
    echo "[deploy] failed to detect logs pipeline python interpreter"
    return 1
  fi
  echo "[deploy] bootstrap logs schema + s3 lifecycle"
  (
    set -euo pipefail
    set -a
    # shellcheck disable=SC1090
    . "$env_file"
    set +a
    cd "$REPO_DIR"
    "$logs_python" scripts/logs_pipeline.py migrate
    "$logs_python" scripts/logs_pipeline.py bootstrap-s3-lifecycle
  )

  if ! can_manage_systemd; then
    echo "[deploy] skip logs pipeline systemd setup (systemd unavailable in current runtime)"
    return 0
  fi

  local units=(
    "blast-logs-hourly.service"
    "blast-logs-hourly.timer"
    "blast-logs-backfill.service"
    "blast-logs-prune.service"
    "blast-logs-prune.timer"
  )

  local repo_escaped
  repo_escaped="${REPO_DIR//|/\\|}"
  repo_escaped="${repo_escaped//&/\\&}"
  local logs_python_escaped
  logs_python_escaped="${logs_python//|/\\|}"
  logs_python_escaped="${logs_python_escaped//&/\\&}"

  local tmp_dir
  tmp_dir="$(mktemp -d)"

  local unit
  for unit in "${units[@]}"; do
    if [[ ! -f "$units_dir/$unit" ]]; then
      echo "[deploy] missing logs systemd unit: $units_dir/$unit"
      return 1
    fi
    sed \
      -e "s|__REPO_DIR__|$repo_escaped|g" \
      -e "s|python3 scripts/logs_pipeline.py|$logs_python_escaped scripts/logs_pipeline.py|g" \
      "$units_dir/$unit" > "$tmp_dir/$unit"
  done

  echo "[deploy] install logs pipeline env -> $target_env_file"
  run_as_root mkdir -p /etc/blast
  run_as_root install -m 600 "$env_file" "$target_env_file"

  for unit in "${units[@]}"; do
    echo "[deploy] install systemd unit /etc/systemd/system/$unit"
    run_as_root install -m 644 "$tmp_dir/$unit" "/etc/systemd/system/$unit"
  done

  echo "[deploy] systemctl daemon-reload"
  if ! run_as_root systemctl daemon-reload; then
    echo "[deploy] skip logs pipeline systemd setup (systemd unavailable in current runtime)"
    rm -rf "$tmp_dir"
    return 0
  fi
  echo "[deploy] enable --now blast-logs-hourly.timer"
  run_as_root systemctl enable --now blast-logs-hourly.timer
  echo "[deploy] enable --now blast-logs-prune.timer"
  run_as_root systemctl enable --now blast-logs-prune.timer

  rm -rf "$tmp_dir"
}

show_status() {
  echo "[deploy] docker compose ps"
  docker compose ps
  if [[ -d "$RUNNERS_DIR" ]]; then
    if [[ -f "$RUNNERS_DIR/.env.dozzle" ]]; then
      docker compose -f "$RUNNERS_DIR/docker-compose.logs.yml" --env-file "$RUNNERS_DIR/.env.dozzle" ps || true
    fi
    if [[ -f "$RUNNERS_DIR/.env.dozzle-agent" ]]; then
      docker compose -f "$RUNNERS_DIR/docker-compose.dozzle-agent.yml" --env-file "$RUNNERS_DIR/.env.dozzle-agent" ps || true
    fi
    if [[ -f "$RUNNERS_DIR/.env.observability" ]]; then
      docker compose -f "$RUNNERS_DIR/docker-compose.observability.yml" --env-file "$RUNNERS_DIR/.env.observability" ps || true
    fi
    if [[ -f "$RUNNERS_DIR/.env.github-runner" ]]; then
      docker compose -f "$RUNNERS_DIR/docker-compose.github-runner.yml" --env-file "$RUNNERS_DIR/.env.github-runner" ps || true
    fi
    if [[ -f "$RUNNERS_DIR/.env.promtail-edge" ]]; then
      docker compose -f "$RUNNERS_DIR/docker-compose.promtail-edge.yml" --env-file "$RUNNERS_DIR/.env.promtail-edge" ps || true
    fi
  fi
}

case "$DEPLOY_STACK" in
  all)
    deploy_root_services
    ;;
  prod-path)
    bootstrap_tg_webhook_ip_env
    deploy_prod_path_services
    dozzle_agent_status=0
    dozzle_agent_env_is_ready || dozzle_agent_status=$?
    if [[ "$dozzle_agent_status" -eq 0 ]]; then
      deploy_runner_compose_if_present "$RUNNERS_DIR/docker-compose.dozzle-agent.yml" "$RUNNERS_DIR/.env.dozzle-agent"
    elif [[ "$dozzle_agent_status" -gt 1 ]]; then
      exit 1
    fi
    deploy_runner_compose_if_present "$RUNNERS_DIR/docker-compose.promtail-edge.yml" "$RUNNERS_DIR/.env.promtail-edge"
    if is_true "$DEPLOY_PRUNE_OTHER_STACK"; then
      stop_root_services tg-bot tg-bot-public-admin asset-ui finance-bot
    fi
    ;;
  infra-apps)
    bootstrap_infra_orchestrator_url
    require_infra_ops_public_admin_env
    mapfile -t services < <(infra_app_services)
    deploy_root_services "${services[@]}"
    if is_true "$DEPLOY_PRUNE_OTHER_STACK"; then
      remove_root_services orchestrator-api orchestrator-api-2 worker-build worker-render worker-render-poll tg-bot-public
    fi
    ;;
  infra-ops)
    bootstrap_infra_orchestrator_url
    require_infra_ops_public_admin_env
    mapfile -t services < <(infra_app_services)
    deploy_root_services "${services[@]}"
    dozzle_central_status=0
    dozzle_central_env_is_ready || dozzle_central_status=$?
    if [[ "$dozzle_central_status" -eq 0 ]]; then
      deploy_runner_compose_if_present "$RUNNERS_DIR/docker-compose.logs.yml" "$RUNNERS_DIR/.env.dozzle"
    elif [[ "$dozzle_central_status" -gt 1 ]]; then
      exit 1
    fi
    deploy_runner_compose_if_present "$RUNNERS_DIR/docker-compose.observability.yml" "$RUNNERS_DIR/.env.observability"
    deploy_github_runner_compose_if_allowed "$RUNNERS_DIR/docker-compose.github-runner.yml" "$RUNNERS_DIR/.env.github-runner"
    if is_true "$DEPLOY_PRUNE_OTHER_STACK"; then
      remove_root_services orchestrator-api orchestrator-api-2 worker-build worker-render worker-render-poll tg-bot-public
    fi
    ;;
  *)
    echo "Unknown DEPLOY_STACK=$DEPLOY_STACK"
    echo "Allowed: all | prod-path | infra-apps | infra-ops"
    exit 1
    ;;
esac

deploy_logs_pipeline_systemd_if_present

show_status
