#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <username> <password> [email] [display_name]"
  exit 1
fi

USERNAME="$1"
PASSWORD="$2"
EMAIL="${3:-admin@local}"
DISPLAY_NAME="${4:-Dozzle Admin}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/dozzle_data"
USERS_FILE="$DATA_DIR/users.yml"

mkdir -p "$DATA_DIR"

docker run --rm amir20/dozzle:v10.4.1 \
  generate "$USERNAME" \
  --password "$PASSWORD" \
  --email "$EMAIL" \
  --name "$DISPLAY_NAME" > "$USERS_FILE"

chmod 600 "$USERS_FILE"

echo "Dozzle users file generated: $USERS_FILE"
