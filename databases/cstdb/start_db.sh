#!/bin/bash
# Start CST persistent data stores

# Resolve repo root relative to this script (databases/cstdb/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${PREFLIGHT_ENV_FILE:-$REPO_ROOT/config/preflight.env}"

if [ ! -f "$ENV_FILE" ]; then
	echo "Missing env file: $ENV_FILE" >&2
	exit 1
fi

docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/docker-compose.yaml" up -d --remove-orphans
