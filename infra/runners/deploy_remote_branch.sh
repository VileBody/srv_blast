#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-${GITHUB_REF_NAME:-}}"
DEPLOY_STACK="${2:-prod-path}"

DEPLOY_REMOTE_HOST="${DEPLOY_REMOTE_HOST:-}"
DEPLOY_REMOTE_USER="${DEPLOY_REMOTE_USER:-deploy}"
DEPLOY_REMOTE_PORT="${DEPLOY_REMOTE_PORT:-22}"
DEPLOY_REMOTE_REPO_DIR="${DEPLOY_REMOTE_REPO_DIR:-}"
DEPLOY_REMOTE_SSH_KEY_PATH="${DEPLOY_REMOTE_SSH_KEY_PATH:-}"

DEPLOY_PRUNE_OTHER_STACK="${DEPLOY_PRUNE_OTHER_STACK:-false}"
DEPLOY_ORCHESTRATOR_HA="${DEPLOY_ORCHESTRATOR_HA:-false}"
DEPLOY_ORCHESTRATOR_HA_COMPOSE_FILE="${DEPLOY_ORCHESTRATOR_HA_COMPOSE_FILE:-docker-compose.orchestrator-ha.yml}"

if [[ -z "$BRANCH" ]]; then
  echo "[deploy-remote] Branch is not specified."
  exit 1
fi
if [[ -z "$DEPLOY_REMOTE_HOST" ]]; then
  echo "[deploy-remote] DEPLOY_REMOTE_HOST is required."
  exit 1
fi
if [[ -z "$DEPLOY_REMOTE_REPO_DIR" ]]; then
  echo "[deploy-remote] DEPLOY_REMOTE_REPO_DIR is required."
  exit 1
fi
if [[ -z "$DEPLOY_REMOTE_SSH_KEY_PATH" ]]; then
  echo "[deploy-remote] DEPLOY_REMOTE_SSH_KEY_PATH is required."
  exit 1
fi
if [[ ! -f "$DEPLOY_REMOTE_SSH_KEY_PATH" ]]; then
  echo "[deploy-remote] SSH key file not found: $DEPLOY_REMOTE_SSH_KEY_PATH"
  exit 1
fi

AUTH_TOKEN="${GIT_AUTH_TOKEN:-${GITHUB_TOKEN_FALLBACK:-}}"
if [[ -z "$AUTH_TOKEN" ]]; then
  echo "[deploy-remote] GitHub token is missing for non-interactive deploy."
  exit 1
fi

AUTH_B64="$(printf '%s' "$AUTH_TOKEN" | base64 | tr -d '\n')"

printf -v q_repo '%q' "$DEPLOY_REMOTE_REPO_DIR"
printf -v q_branch '%q' "$BRANCH"
printf -v q_stack '%q' "$DEPLOY_STACK"
printf -v q_prune '%q' "$DEPLOY_PRUNE_OTHER_STACK"
printf -v q_ha '%q' "$DEPLOY_ORCHESTRATOR_HA"
printf -v q_ha_file '%q' "$DEPLOY_ORCHESTRATOR_HA_COMPOSE_FILE"
printf -v q_auth_b64 '%q' "$AUTH_B64"

remote_cmd=$(
  cat <<EOF
set -euo pipefail
export REPO_DIR=$q_repo
export DEPLOY_PRUNE_OTHER_STACK=$q_prune
export DEPLOY_ORCHESTRATOR_HA=$q_ha
export DEPLOY_ORCHESTRATOR_HA_COMPOSE_FILE=$q_ha_file
export GIT_AUTH_TOKEN=\$(printf '%s' $q_auth_b64 | base64 -d)
bash "$DEPLOY_REMOTE_REPO_DIR/infra/runners/deploy_branch.sh" $q_branch $q_stack
EOF
)
printf -v q_remote_cmd '%q' "$remote_cmd"

echo "[deploy-remote] host=$DEPLOY_REMOTE_USER@$DEPLOY_REMOTE_HOST port=$DEPLOY_REMOTE_PORT repo=$DEPLOY_REMOTE_REPO_DIR branch=$BRANCH stack=$DEPLOY_STACK"

if command -v ssh-keygen >/dev/null 2>&1; then
  ssh-keygen -R "$DEPLOY_REMOTE_HOST" >/dev/null 2>&1 || true
  ssh-keygen -R "[$DEPLOY_REMOTE_HOST]:$DEPLOY_REMOTE_PORT" >/dev/null 2>&1 || true
fi

ssh \
  -p "$DEPLOY_REMOTE_PORT" \
  -i "$DEPLOY_REMOTE_SSH_KEY_PATH" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=accept-new \
  -o ConnectTimeout=20 \
  "$DEPLOY_REMOTE_USER@$DEPLOY_REMOTE_HOST" \
  "bash -lc $q_remote_cmd"

echo "[deploy-remote] done"
