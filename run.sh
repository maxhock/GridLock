#!/bin/bash
set -e

# Build the compose generator image
docker build -f composegen/Dockerfile -t composegen composegen

# Run the generator, mounting only the config folder so docker-compose.yaml is written to config/
docker run --rm -v "$(pwd)/config:/config" composegen

# Now launch the experiment with docker compose, pointing at the generated file
docker compose -f config/tmp/docker-compose.yml up --build --remove-orphans