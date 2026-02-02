#!/bin/bash
set -e

echo "Loading environment variables from .env file..."
set -a
[ -f $(dirname "$0")/../../.env ] && . $(dirname "$0")/../../.env
set +a

PARAM="${1:-$AGS}"
OPTIONS="${2:-}"

echo "Starting docker compose..."
export AGS="$PARAM"

# echo "Starting docker compose..."
docker compose -f "$(dirname "$0")/compose.yml" up $OPTIONS
