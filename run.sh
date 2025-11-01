#!/bin/bash
set -e

echo "=== GridLock Co-Simulation Runner (HELICS Runner Method) ==="

# Step 1: Build the configuration generator
echo "Building configuration generator..."
docker build -f composegen/Dockerfile -t gridlock-composegen composegen

# Step 2: Run the generator to create helics_runner configs per federate class
echo "Generating helics_runner configurations and docker-compose..."
docker run --rm \
  -v "$(pwd)/config:/config" \
  -v "$(pwd)/data:/data" \
  gridlock-composegen

# Step 3: Launch the co-simulation using docker-compose
# Each container runs helics_runner with its class-specific config
echo "Starting co-simulation with helics_runner (one container per federate class)..."
docker compose -f config/tmp/docker-compose.yml up --build --remove-orphans

echo "=== Simulation complete ==="

# To stop and clean up:
# docker compose -f config/tmp/docker-compose.yml down --remove-orphans