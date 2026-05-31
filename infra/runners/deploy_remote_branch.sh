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
PROD_TG_WEBHOOK_IP_ADDRESS="${PROD_TG_WEBHOOK_IP_ADDRESS:-}"
DEPLOY_USE_PREBUILT_IMAGES="${DEPLOY_USE_PREBUILT_IMAGES:-false}"
BLAST_IMAGE_REGISTRY="${BLAST_IMAGE_REGISTRY:-ghcr.io}"
BLAST_IMAGE_REGISTRY_USERNAME="${BLAST_IMAGE_REGISTRY_USERNAME:-}"
BLAST_RUNTIME_IMAGE="${BLAST_RUNTIME_IMAGE:-}"
BLAST_TG_BOT_IMAGE="${BLAST_TG_BOT_IMAGE:-}"
BLAST_TG_BOT_PUBLIC_IMAGE="${BLAST_TG_BOT_PUBLIC_IMAGE:-}"
BLAST_ASSET_UI_IMAGE="${BLAST_ASSET_UI_IMAGE:-}"
BLAST_FINANCE_BOT_IMAGE="${BLAST_FINANCE_BOT_IMAGE:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_DEPLOY_BRANCH_SCRIPT="$SCRIPT_DIR/deploy_branch.sh"

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
printf -v q_prod_tg_webhook_ip_address '%q' "$PROD_TG_WEBHOOK_IP_ADDRESS"
printf -v q_use_prebuilt '%q' "$DEPLOY_USE_PREBUILT_IMAGES"
printf -v q_image_registry '%q' "$BLAST_IMAGE_REGISTRY"
printf -v q_image_registry_username '%q' "$BLAST_IMAGE_REGISTRY_USERNAME"
printf -v q_runtime_image '%q' "$BLAST_RUNTIME_IMAGE"
printf -v q_tg_bot_image '%q' "$BLAST_TG_BOT_IMAGE"
printf -v q_tg_bot_public_image '%q' "$BLAST_TG_BOT_PUBLIC_IMAGE"
printf -v q_asset_ui_image '%q' "$BLAST_ASSET_UI_IMAGE"
printf -v q_finance_bot_image '%q' "$BLAST_FINANCE_BOT_IMAGE"
printf -v q_auth_b64 '%q' "$AUTH_B64"

remote_cmd=$(
  cat <<EOF
set -euo pipefail
export REPO_DIR=$q_repo
export DEPLOY_PRUNE_OTHER_STACK=$q_prune
export DEPLOY_ORCHESTRATOR_HA=$q_ha
export DEPLOY_ORCHESTRATOR_HA_COMPOSE_FILE=$q_ha_file
export PROD_TG_WEBHOOK_IP_ADDRESS=$q_prod_tg_webhook_ip_address
export DEPLOY_USE_PREBUILT_IMAGES=$q_use_prebuilt
export BLAST_IMAGE_REGISTRY=$q_image_registry
export BLAST_IMAGE_REGISTRY_USERNAME=$q_image_registry_username
export BLAST_RUNTIME_IMAGE=$q_runtime_image
export BLAST_TG_BOT_IMAGE=$q_tg_bot_image
export BLAST_TG_BOT_PUBLIC_IMAGE=$q_tg_bot_public_image
export BLAST_ASSET_UI_IMAGE=$q_asset_ui_image
export BLAST_FINANCE_BOT_IMAGE=$q_finance_bot_image
export GIT_AUTH_TOKEN=\$(printf '%s' $q_auth_b64 | base64 -d)
bash -s -- $q_branch $q_stack
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
  "bash -lc $q_remote_cmd" < "$REMOTE_DEPLOY_BRANCH_SCRIPT"

echo "[deploy-remote] done"
