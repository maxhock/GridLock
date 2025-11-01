# PowerShell equivalent of run.sh
$ErrorActionPreference = "Stop"

Write-Host "=== GridLock Co-Simulation Runner (HELICS Runner Method) ===" -ForegroundColor Green

# Step 1: Build the configuration generator
Write-Host "Building configuration generator..." -ForegroundColor Cyan
docker build -f composegen/Dockerfile -t gridlock-composegen composegen

# Step 2: Run the generator to create helics_runner configs per federate class
Write-Host "Generating helics_runner configurations and docker-compose..." -ForegroundColor Cyan
docker run --rm `
  -v "${PSScriptRoot}/config:/config" `
  -v "${PSScriptRoot}/data:/data" `
  gridlock-composegen

# Step 3: Launch the co-simulation using docker-compose
# Each container runs helics_runner with its class-specific config
Write-Host "Starting co-simulation with helics_runner (one container per federate class)..." -ForegroundColor Cyan
docker compose -f config/tmp/docker-compose.yml up --build --remove-orphans

Write-Host "=== Simulation complete ===" -ForegroundColor Green

# To stop and clean up:
# docker compose -f config/tmp/docker-compose.yml down --remove-orphans