#!/bin/bash
set -e

# Build the compose generator image
docker build -f ./composegen/Dockerfile -t composegen composegen

# Run the generator
docker run --rm -v "$(pwd)/config:/config" -v "$(pwd)/data:/data" -v "$(pwd)/meta_store:/app/meta_store" composegen

# Now launch the experiment with docker compose, pointing at the generated file
# Find newest yaml in meta_store and use it as the compose file (fallback to meta_store/docker-compose.yml)
LATEST_YAML=$(ls -t meta_store/*.yml meta_store/*.yaml 2>/dev/null | head -n1 || true)
COMPOSE_FILE=${LATEST_YAML:-meta_store/docker-compose.yml}
docker compose -f "$COMPOSE_FILE" up --build --remove-orphans

# docker compose -f config/tmp/docker-compose.yml down --remove-orphans