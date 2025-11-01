#!/bin/bash
set -e

echo "=== GridLock Co-Simulation Runner (HELICS Runner Method) ==="

# Step 1: Build the configuration generator
echo "Building configuration generator..."
docker build -f composegen/Dockerfile -t gridlock-composegen composegen

# Step 2: Run the generator to create helics_runner.json
echo "Generating helics_runner.json configuration..."
docker run --rm \
  -v "$(pwd)/config:/config" \
  -v "$(pwd)/data:/data" \
  gridlock-composegen

# Step 3: Build the runner container (contains all federates + helics_runner)
echo "Building simulation runner container..."
docker build -f runner/Dockerfile -t gridlock-runner runner

# Step 4: Run the simulation using helics_runner
echo "Starting co-simulation with helics_runner..."
docker run --rm \
  -v "$(pwd)/config:/config" \
  -v "$(pwd)/data:/data" \
  gridlock-runner

echo "=== Simulation complete ==="

# Note: For backward compatibility, docker-compose.yml is still generated
# To use the old method, run: docker compose -f config/tmp/docker-compose.yml up --build