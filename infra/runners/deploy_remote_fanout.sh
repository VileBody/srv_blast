#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-${GITHUB_REF_NAME:-}}"
DEPLOY_STACK="${2:-prod-path}"

DEPLOY_REMOTE_NODE0_HOST="${DEPLOY_REMOTE_NODE0_HOST:-}"
DEPLOY_REMOTE_NODE0_USER="${DEPLOY_REMOTE_NODE0_USER:-deploy}"
DEPLOY_REMOTE_NODE0_PORT="${DEPLOY_REMOTE_NODE0_PORT:-22}"
DEPLOY_REMOTE_NODE0_REPO_DIR="${DEPLOY_REMOTE_NODE0_REPO_DIR:-}"
DEPLOY_REMOTE_NODE0_SSH_KEY_PATH="${DEPLOY_REMOTE_NODE0_SSH_KEY_PATH:-}"

DEPLOY_REMOTE_NODE1_HOST="${DEPLOY_REMOTE_NODE1_HOST:-}"
DEPLOY_REMOTE_NODE1_USER="${DEPLOY_REMOTE_NODE1_USER:-deploy}"
DEPLOY_REMOTE_NODE1_PORT="${DEPLOY_REMOTE_NODE1_PORT:-22}"
DEPLOY_REMOTE_NODE1_REPO_DIR="${DEPLOY_REMOTE_NODE1_REPO_DIR:-}"
DEPLOY_REMOTE_NODE1_SSH_KEY_PATH="${DEPLOY_REMOTE_NODE1_SSH_KEY_PATH:-}"
PROD_TG_WEBHOOK_IP_ADDRESS="${PROD_TG_WEBHOOK_IP_ADDRESS:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_DEPLOY_SCRIPT="$SCRIPT_DIR/deploy_remote_branch.sh"

if [[ -z "$BRANCH" ]]; then
  echo "[deploy-fanout] Branch is not specified."
  exit 1
fi
if [[ "$DEPLOY_STACK" != "prod-path" ]]; then
  echo "[deploy-fanout] DEPLOY_STACK must be prod-path for fan-out deploy (got: $DEPLOY_STACK)"
  exit 1
fi
if [[ ! -x "$REMOTE_DEPLOY_SCRIPT" ]]; then
  echo "[deploy-fanout] Missing executable script: $REMOTE_DEPLOY_SCRIPT"
  exit 1
fi

deploy_node() {
  local node_name="$1"
  local host="$2"
  local user="$3"
  local port="$4"
  local repo_dir="$5"
  local key_path="$6"

  if [[ -z "$host" ]]; then
    echo "[deploy-fanout] $node_name host is required"
    exit 1
  fi
  if [[ -z "$repo_dir" ]]; then
    echo "[deploy-fanout] $node_name repo dir is required"
    exit 1
  fi
  if [[ -z "$key_path" ]]; then
    echo "[deploy-fanout] $node_name ssh key path is required"
    exit 1
  fi
  if [[ ! -f "$key_path" ]]; then
    echo "[deploy-fanout] $node_name ssh key file not found: $key_path"
    exit 1
  fi

  echo "[deploy-fanout] deploying branch=$BRANCH stack=$DEPLOY_STACK node=$node_name host=$host"
  DEPLOY_REMOTE_HOST="$host" \
  DEPLOY_REMOTE_USER="$user" \
  DEPLOY_REMOTE_PORT="$port" \
  DEPLOY_REMOTE_REPO_DIR="$repo_dir" \
  DEPLOY_REMOTE_SSH_KEY_PATH="$key_path" \
  PROD_TG_WEBHOOK_IP_ADDRESS="$PROD_TG_WEBHOOK_IP_ADDRESS" \
  "$REMOTE_DEPLOY_SCRIPT" "$BRANCH" "$DEPLOY_STACK"
}

deploy_node \
  "orchestrator-0" \
  "$DEPLOY_REMOTE_NODE0_HOST" \
  "$DEPLOY_REMOTE_NODE0_USER" \
  "$DEPLOY_REMOTE_NODE0_PORT" \
  "$DEPLOY_REMOTE_NODE0_REPO_DIR" \
  "$DEPLOY_REMOTE_NODE0_SSH_KEY_PATH"

deploy_node \
  "orchestrator-1" \
  "$DEPLOY_REMOTE_NODE1_HOST" \
  "$DEPLOY_REMOTE_NODE1_USER" \
  "$DEPLOY_REMOTE_NODE1_PORT" \
  "$DEPLOY_REMOTE_NODE1_REPO_DIR" \
  "$DEPLOY_REMOTE_NODE1_SSH_KEY_PATH"

echo "[deploy-fanout] done"
