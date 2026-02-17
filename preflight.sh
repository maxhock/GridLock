#!/bin/bash
set -e

# Preflight: runs infdb data setup and composegen to prepare configs and data.
# Used by run.sh and as devcontainer initializeCommand.
# Requires Docker CLI on the machine where it runs.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Step 1: Run infdb data setup to resolve grids and write to CST metadata store
echo "=== Preflight Step 1: infdb data setup ==="
docker build -f ./databases/infdb/Dockerfile -t infdb databases/infdb
docker run --rm \
  -v "$(pwd)/databases/infdb/configs:/app/configs:ro" \
  -v "$(pwd)/meta_store:/app/meta_store" \
  -v "$(pwd)/config:/config:ro" \
  --env-file "$(pwd)/databases/infdb/.env" \
  --add-host=host.docker.internal:host-gateway \
  infdb

# Step 2: Build and run composegen (reads manifest.json from meta_store)
echo "=== Preflight Step 2: composegen ==="
docker build -f ./composegen/Dockerfile -t composegen composegen
docker run --rm \
  -v "$(pwd)/config:/config" \
  -v "$(pwd)/data:/data" \
  -v "$(pwd)/meta_store:/app/meta_store" \
  composegen

echo "=== Preflight complete ==="
