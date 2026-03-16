#!/bin/bash
# Stop CST persistent data stores
# Add '--volumes' to also remove persistent data volumes

# Resolve repo root relative to this script (databases/cstdb/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

source "$REPO_ROOT/cosim.env"
docker compose -f "$STACK_DIR/docker-compose.yaml" down --remove-orphans
