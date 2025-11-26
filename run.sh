#!/bin/bash
set -e

# Build and run data generation
docker build -f data-gen/Dockerfile -t data-gen data-gen
docker run --rm -v "$(pwd)/config:/config" -v "$(pwd)/data:/data" data-gen

# Build the compose generator image
docker build -f composegen/Dockerfile -t composegen composegen

# Run the generator
docker run --rm -v "$(pwd)/config:/config" -v "$(pwd)/data:/data" composegen

# Now launch the experiment with docker compose, pointing at the generated file
docker compose -f config/tmp/docker-compose.yml up --build --remove-orphans

# docker compose -f config/tmp/docker-compose.yml down --remove-orphans