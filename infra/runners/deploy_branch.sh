#!/usr/bin/env bash
set -euo pipefail

BRANCH="${1:-${GITHUB_REF_NAME:-}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

if [[ -z "$BRANCH" ]]; then
  echo "Branch is not specified. Pass it as the first argument."
  exit 1
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "REPO_DIR is not a git repository: $REPO_DIR"
  exit 1
fi

cd "$REPO_DIR"

echo "[deploy] repo=$REPO_DIR branch=$BRANCH"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "[deploy] docker compose up -d --build"
docker compose up -d --build

echo "[deploy] docker compose ps"
docker compose ps

