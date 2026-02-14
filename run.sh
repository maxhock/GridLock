#!/bin/bash
set -e

# Step 1: Run infdb data setup to resolve grids and write to CST metadata store
echo "=== Step 1: infdb data setup ==="
docker build -f ./databases/infdb/Dockerfile -t infdb databases/infdb
docker run --rm \
  -v "$(pwd)/databases/infdb/configs:/workspaces/infdb/configs:ro" \
  -v "$(pwd)/meta_store:/workspaces/infdb/meta_store" \
  -v "$(pwd)/config:/config:ro" \
  --env-file databases/infdb/.env \
  --add-host=host.docker.internal:host-gateway \
  infdb

# Step 2: Build and run composegen (reads manifest.json from meta_store)
echo "=== Step 2: composegen ==="
docker build -f ./composegen/Dockerfile -t composegen composegen
docker run --rm \
  -v "$(pwd)/config:/config" \
  -v "$(pwd)/data:/data" \
  -v "$(pwd)/meta_store:/app/meta_store" \
  composegen

# Step 3: Launch the experiment with docker compose
echo "=== Step 3: docker compose up ==="
LATEST_YAML=$(ls -t meta_store/*.yml meta_store/*.yaml 2>/dev/null | head -n1 || true)
COMPOSE_FILE=${LATEST_YAML:-meta_store/docker-compose.yml}
docker compose -f "$COMPOSE_FILE" up --build --remove-orphans