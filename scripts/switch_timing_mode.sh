#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
FAST_START="${2:-6}"

if [[ "${MODE}" != "prompts" && "${MODE}" != "hybrid" ]]; then
  echo "Usage: $0 <prompts|hybrid> [fast_start_seconds]"
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing .env at ${ENV_FILE}"
  exit 1
fi

python3 - "${ENV_FILE}" "${MODE}" "${FAST_START}" <<'PY'
from __future__ import annotations

import pathlib
import re
import sys

env_path = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
fast = sys.argv[3]

text = env_path.read_text(encoding="utf-8")

def upsert(src: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", flags=re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(src):
        return pattern.sub(line, src)
    src = src.rstrip("\n")
    if src:
        src += "\n"
    return src + line + "\n"

text = upsert(text, "STAGE2_TIMING_MODE", mode)
text = upsert(text, "STAGE2_FAST_START_SECONDS", fast)
env_path.write_text(text, encoding="utf-8")
PY

cd "${REPO_ROOT}"
docker compose up -d --force-recreate orchestrator-api worker-build worker-render
docker compose exec -T orchestrator-api /bin/sh -lc 'echo "STAGE2_TIMING_MODE=${STAGE2_TIMING_MODE} STAGE2_FAST_START_SECONDS=${STAGE2_FAST_START_SECONDS}"'

