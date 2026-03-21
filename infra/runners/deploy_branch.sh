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

# Self-hosted runner containers may run under a different UID than the
# mounted repository owner. Mark repo as safe to avoid "dubious ownership".
if ! git config --global --get-all safe.directory 2>/dev/null | grep -Fxq "$REPO_DIR"; then
  git config --global --add safe.directory "$REPO_DIR"
fi

git_run() {
  if [[ -n "${GIT_AUTH_TOKEN:-}" ]]; then
    git \
      -c "url.https://x-access-token:${GIT_AUTH_TOKEN}@github.com/.insteadof=git@github.com:" \
      -c "url.https://x-access-token:${GIT_AUTH_TOKEN}@github.com/.insteadof=ssh://git@github.com/" \
      -c "url.https://x-access-token:${GIT_AUTH_TOKEN}@github.com/.insteadof=https://github.com/" \
      "$@"
  else
    git "$@"
  fi
}

git_run fetch origin "$BRANCH"
git_run checkout "$BRANCH"
git_run pull --ff-only origin "$BRANCH"

echo "[deploy] docker compose up -d --build"
docker compose up -d --build

echo "[deploy] docker compose ps"
docker compose ps
