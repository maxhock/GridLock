#!/bin/bash
set -e

# Build the compose generator image
docker build -f composegen/Dockerfile -t composegen composegen

# Run the generator
docker run --rm -v "$(pwd)/config:/config" -v "$(pwd)/data:/data" -v "$(pwd)/meta_store:/app/meta_store" composegen

# Now launch the experiment with docker compose, pointing at the generated file
docker compose -f config/tmp/docker-compose.yml up --build --remove-orphans

# docker compose -f config/tmp/docker-compose.yml down --remove-orphans