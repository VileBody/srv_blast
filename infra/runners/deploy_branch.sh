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

# Some branch lines introduce new tracked files that may already exist on the
# server as untracked leftovers from previous deploy attempts.
# If we keep them, `git checkout` can fail with:
#   "untracked working tree files would be overwritten by checkout"
LEGACY_UNTRACKED_CONFLICTS=(
  "services/tg_bot_botapi/migrations/001_init.sql"
)
for p in "${LEGACY_UNTRACKED_CONFLICTS[@]}"; do
  if [[ -e "$p" ]] && ! git ls-files --error-unmatch "$p" >/dev/null 2>&1; then
    echo "[deploy] remove untracked checkout conflict: $p"
    rm -f -- "$p"
  fi
done

git_run fetch origin "$BRANCH"
git_run checkout "$BRANCH"
git_run pull --ff-only origin "$BRANCH"

echo "[deploy] docker compose up -d --build"
docker compose up -d --build

echo "[deploy] docker compose ps"
docker compose ps
