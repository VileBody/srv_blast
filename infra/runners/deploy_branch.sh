#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-${GITHUB_REF_NAME:-}}"
DEPLOY_STACK="${2:-${DEPLOY_STACK:-all}}"
DEPLOY_PRUNE_OTHER_STACK="${DEPLOY_PRUNE_OTHER_STACK:-false}"
DEPLOY_ORCHESTRATOR_HA="${DEPLOY_ORCHESTRATOR_HA:-false}"
DEPLOY_ORCHESTRATOR_HA_COMPOSE_FILE="${DEPLOY_ORCHESTRATOR_HA_COMPOSE_FILE:-docker-compose.orchestrator-ha.yml}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
RUNNERS_DIR="$REPO_DIR/infra/runners"

is_true() {
  local v
  v="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "on" ]]
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
    deploy_root_services orchestrator-api worker-build worker-render tg-bot-public
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
  echo "[deploy] docker compose -f docker-compose.yml -f $compose_ha up -d --build orchestrator-api orchestrator-api-2 worker-build worker-render tg-bot-public"
  docker compose -f docker-compose.yml -f "$compose_ha" up -d --build \
    orchestrator-api orchestrator-api-2 worker-build worker-render tg-bot-public
}

stop_root_services() {
  local services=("$@")
  if [[ ${#services[@]} -eq 0 ]]; then
    return 0
  fi
  echo "[deploy] docker compose stop ${services[*]}"
  docker compose stop "${services[@]}" || true
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

show_status() {
  echo "[deploy] docker compose ps"
  docker compose ps
  if [[ -d "$RUNNERS_DIR" ]]; then
    if [[ -f "$RUNNERS_DIR/.env.dozzle" ]]; then
      docker compose -f "$RUNNERS_DIR/docker-compose.logs.yml" --env-file "$RUNNERS_DIR/.env.dozzle" ps || true
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
    deploy_prod_path_services
    deploy_runner_compose_if_present "$RUNNERS_DIR/docker-compose.promtail-edge.yml" "$RUNNERS_DIR/.env.promtail-edge"
    if is_true "$DEPLOY_PRUNE_OTHER_STACK"; then
      stop_root_services tg-bot asset-ui finance-bot
    fi
    ;;
  infra-apps)
    deploy_root_services tg-bot asset-ui finance-bot
    if is_true "$DEPLOY_PRUNE_OTHER_STACK"; then
      stop_root_services orchestrator-api orchestrator-api-2 worker-build worker-render tg-bot-public
    fi
    ;;
  infra-ops)
    deploy_root_services tg-bot asset-ui finance-bot
    deploy_runner_compose_if_present "$RUNNERS_DIR/docker-compose.logs.yml" "$RUNNERS_DIR/.env.dozzle"
    deploy_runner_compose_if_present "$RUNNERS_DIR/docker-compose.observability.yml" "$RUNNERS_DIR/.env.observability"
    deploy_github_runner_compose_if_allowed "$RUNNERS_DIR/docker-compose.github-runner.yml" "$RUNNERS_DIR/.env.github-runner"
    if is_true "$DEPLOY_PRUNE_OTHER_STACK"; then
      stop_root_services orchestrator-api orchestrator-api-2 worker-build worker-render tg-bot-public
    fi
    ;;
  *)
    echo "Unknown DEPLOY_STACK=$DEPLOY_STACK"
    echo "Allowed: all | prod-path | infra-apps | infra-ops"
    exit 1
    ;;
esac

show_status
