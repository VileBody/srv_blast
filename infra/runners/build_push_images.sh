#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

BLAST_IMAGE_REGISTRY="${BLAST_IMAGE_REGISTRY:-ghcr.io}"
BLAST_IMAGE_PLATFORM="${BLAST_IMAGE_PLATFORM:-linux/amd64}"
BLAST_IMAGE_TAG="${BLAST_IMAGE_TAG:-}"
BLAST_IMAGE_PREFIX="${BLAST_IMAGE_PREFIX:-}"
BLAST_IMAGE_OWNER="${BLAST_IMAGE_OWNER:-${GITHUB_REPOSITORY_OWNER:-}}"
BLAST_IMAGE_REGISTRY_USERNAME="${BLAST_IMAGE_REGISTRY_USERNAME:-${GITHUB_ACTOR:-}}"
BLAST_IMAGE_REGISTRY_TOKEN="${BLAST_IMAGE_REGISTRY_TOKEN:-${GITHUB_TOKEN:-${GH_TOKEN:-}}}"
BLAST_IMAGE_REBUILD_EXISTING="${BLAST_IMAGE_REBUILD_EXISTING:-false}"
BLAST_IMAGE_DRY_RUN="${BLAST_IMAGE_DRY_RUN:-false}"

is_true() {
  local v
  v="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "on" ]]
}

detect_github_owner() {
  local remote
  remote="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)"
  case "$remote" in
    git@github.com:*)
      remote="${remote#git@github.com:}"
      ;;
    ssh://git@github.com/*)
      remote="${remote#ssh://git@github.com/}"
      ;;
    https://github.com/*)
      remote="${remote#https://github.com/}"
      ;;
    *)
      remote=""
      ;;
  esac
  remote="${remote%%/*}"
  remote="${remote%.git}"
  printf '%s' "$remote"
}

image_exists_for_platform() {
  local image="$1"
  local manifest
  manifest="$(docker manifest inspect "$image" 2>/dev/null || true)"
  if [[ -z "$manifest" ]]; then
    return 1
  fi
  python3 -c '
import json
import sys

want = sys.argv[1]
try:
    want_os, want_arch = want.split("/", 1)
except ValueError:
    sys.exit(1)

try:
    manifest = json.load(sys.stdin)
except Exception:
    sys.exit(1)

if "manifests" in manifest:
    for item in manifest.get("manifests") or []:
        platform = item.get("platform") or {}
        if platform.get("os") == want_os and platform.get("architecture") == want_arch:
            sys.exit(0)
    sys.exit(1)

if manifest.get("os") == want_os and manifest.get("architecture") == want_arch:
    sys.exit(0)

sys.exit(1)
' "$BLAST_IMAGE_PLATFORM" <<<"$manifest"
}

docker_login_if_token() {
  if [[ -z "$BLAST_IMAGE_REGISTRY_TOKEN" ]]; then
    echo "[images] registry token not provided; assuming docker is already logged in to $BLAST_IMAGE_REGISTRY"
    return 0
  fi
  if [[ -z "$BLAST_IMAGE_REGISTRY_USERNAME" ]]; then
    echo "[images] BLAST_IMAGE_REGISTRY_USERNAME is required when BLAST_IMAGE_REGISTRY_TOKEN is set"
    return 1
  fi
  echo "[images] docker login $BLAST_IMAGE_REGISTRY as $BLAST_IMAGE_REGISTRY_USERNAME"
  printf '%s\n' "$BLAST_IMAGE_REGISTRY_TOKEN" \
    | docker login "$BLAST_IMAGE_REGISTRY" -u "$BLAST_IMAGE_REGISTRY_USERNAME" --password-stdin >/dev/null
}

build_push_image() {
  local name="$1"
  local dockerfile="$2"
  local context="$3"
  local image="$4"

  if ! is_true "$BLAST_IMAGE_REBUILD_EXISTING" && image_exists_for_platform "$image"; then
    echo "[images] reuse existing $name image for $BLAST_IMAGE_PLATFORM: $image"
    return 0
  fi

  echo "[images] build/push $name image for $BLAST_IMAGE_PLATFORM: $image"
  if is_true "$BLAST_IMAGE_DRY_RUN"; then
    echo docker buildx build --platform "$BLAST_IMAGE_PLATFORM" -f "$dockerfile" -t "$image" --push "$context"
    return 0
  fi
  docker buildx build \
    --platform "$BLAST_IMAGE_PLATFORM" \
    -f "$dockerfile" \
    -t "$image" \
    --push \
    "$context"
}

if ! git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[images] REPO_DIR is not a git repository: $REPO_DIR"
  exit 1
fi

cd "$REPO_DIR"

if [[ -z "$BLAST_IMAGE_TAG" ]]; then
  BLAST_IMAGE_TAG="$(git rev-parse HEAD)"
fi
if [[ -z "$BLAST_IMAGE_OWNER" ]]; then
  BLAST_IMAGE_OWNER="$(detect_github_owner)"
fi
if [[ -z "$BLAST_IMAGE_OWNER" ]]; then
  echo "[images] cannot infer GitHub owner; set BLAST_IMAGE_OWNER or BLAST_IMAGE_PREFIX"
  exit 1
fi
if [[ -z "$BLAST_IMAGE_PREFIX" ]]; then
  owner_lc="$(printf '%s' "$BLAST_IMAGE_OWNER" | tr '[:upper:]' '[:lower:]')"
  BLAST_IMAGE_PREFIX="${BLAST_IMAGE_REGISTRY}/${owner_lc}/srv-blast"
fi

if [[ "$BLAST_IMAGE_PLATFORM" != "linux/amd64" ]]; then
  echo "[images] refusing non-prod platform: $BLAST_IMAGE_PLATFORM"
  echo "[images] set BLAST_IMAGE_PLATFORM=linux/amd64 for production deploy images"
  exit 1
fi

if [[ -n "$(git status --porcelain)" && -z "${BLAST_IMAGE_ALLOW_DIRTY:-}" ]]; then
  echo "[images] working tree is dirty; commit first or set BLAST_IMAGE_ALLOW_DIRTY=1"
  exit 1
fi

docker_login_if_token

build_push_image "runtime" "Dockerfile" "." "${BLAST_IMAGE_PREFIX}/runtime:${BLAST_IMAGE_TAG}"
build_push_image "tg-bot" "Dockerfile.tg-bot" "." "${BLAST_IMAGE_PREFIX}/tg-bot:${BLAST_IMAGE_TAG}"
build_push_image "tg-bot-public" "Dockerfile.tg-bot-public" "." "${BLAST_IMAGE_PREFIX}/tg-bot-public:${BLAST_IMAGE_TAG}"
build_push_image "asset-ui" "Dockerfile.asset-ui" "." "${BLAST_IMAGE_PREFIX}/asset-ui:${BLAST_IMAGE_TAG}"
build_push_image "finance-bot" "finance_bot/Dockerfile" "finance_bot" "${BLAST_IMAGE_PREFIX}/finance-bot:${BLAST_IMAGE_TAG}"

echo "[images] done prefix=$BLAST_IMAGE_PREFIX tag=$BLAST_IMAGE_TAG platform=$BLAST_IMAGE_PLATFORM"
