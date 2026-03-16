#!/bin/bash
# Start CST persistent data stores

# Resolve repo root relative to this script (databases/cstdb/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

source "$REPO_ROOT/cosim.env"
docker compose -f "$STACK_DIR/docker-compose.yaml" up -d --remove-orphans
