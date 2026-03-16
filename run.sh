#!/bin/bash
set -e

# Steps 1-2: Preflight (infdb + composegen)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFLIGHT_ENV_FILE="${PREFLIGHT_ENV_FILE:-$SCRIPT_DIR/config/preflight.env}"

# Source project environment
if [ -f "$PREFLIGHT_ENV_FILE" ]; then
  set -a
  source "$PREFLIGHT_ENV_FILE"
  set +a
elif [ -f "$SCRIPT_DIR/cosim.env" ]; then
  source "$SCRIPT_DIR/cosim.env"
fi

export PREFLIGHT_ENV_FILE
export INFDB_ENV_FILE="${INFDB_ENV_FILE:-$PREFLIGHT_ENV_FILE}"
"$SCRIPT_DIR/preflight.sh"

# Step 3: Launch the experiment with docker compose
echo "=== Step 3: docker compose up ==="
LATEST_YAML=$(ls -t meta_store/*.yml meta_store/*.yaml 2>/dev/null | head -n1 || true)
COMPOSE_FILE=${LATEST_YAML:-meta_store/docker-compose.yml}

# Compose down to handle previous crashed runs with stale networks and containers
if [ -f "$COMPOSE_FILE" ]; then
  docker compose -f "$COMPOSE_FILE" down --remove-orphans 2>/dev/null || true
fi

docker compose -f "$COMPOSE_FILE" up --build --remove-orphans