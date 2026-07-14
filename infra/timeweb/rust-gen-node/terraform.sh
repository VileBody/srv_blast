#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MODULE="$ROOT/infra/timeweb/rust-gen-node"
ENV_FILE="${RUST_GEN_IAC_ENV_FILE:-$ROOT/.env.rust-gen.iac}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Start from infra/timeweb/rust-gen-node/env.rust-gen.iac.example." >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

split_cidrs() {
  local value="$1"
  python3 - "$value" <<'PY'
import json, sys
items = [item.strip() for item in sys.argv[1].split(",") if item.strip()]
print(json.dumps(items))
PY
}

split_numbers() {
  local value="$1"
  python3 - "$value" <<'PY'
import json, sys
items = [int(item.strip()) for item in sys.argv[1].split(",") if item.strip()]
print(json.dumps(items))
PY
}

# The provider reads TWC_TOKEN directly. Passing it through TF_VAR_twc_token breaks
# provider initialization in the current Timeweb provider on darwin_arm64.
export TWC_TOKEN
export TF_VAR_os_id="$TWC_LINUX_OS_ID"
export TF_VAR_preset_id="${TWC_PRESET_ID:-4807}"
export TF_VAR_availability_zone="$TWC_AZ"
export TF_VAR_server_name="${TWC_SERVER_NAME:-blast-rust-gen-1}"
export TF_VAR_enable_public_ipv4="${TWC_ENABLE_PUBLIC_IPV4:-true}"
export TF_VAR_manager_api_cidrs="$(split_cidrs "${RUST_GEN_MANAGER_API_CIDRS:-}")"
export TF_VAR_ssh_allowed_cidrs="$(split_cidrs "${RUST_GEN_SSH_ALLOWED_CIDRS:-}")"
export TF_VAR_ssh_key_ids="$(split_numbers "${TWC_SSH_KEY_IDS:-}")"
if [[ -n "${TWC_PROJECT_ID:-}" ]]; then export TF_VAR_project_id="$TWC_PROJECT_ID"; fi

exec terraform -chdir="$MODULE" "$@"
