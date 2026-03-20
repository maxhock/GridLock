#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
ENV_FILE="$SCRIPT_DIR/../../config/preflight.env"

if [ ! -f "$ENV_FILE" ]; then
	echo "Missing env file: $ENV_FILE" >&2
	exit 1
fi

set -a
. "$ENV_FILE"
set +a

PARAM="${1:-${AGS:-}}"
OPTIONS="${2:-}"

export AGS="$PARAM"

docker compose -f "$SCRIPT_DIR/compose.yml" up $OPTIONS
