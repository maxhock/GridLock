# PowerShell equivalent of run.sh
$ErrorActionPreference = "Stop"

Write-Host "=== GridLock Co-Simulation Runner (HELICS Runner Method) ===" -ForegroundColor Green

# Step 1: Build the configuration generator
Write-Host "Building configuration generator..." -ForegroundColor Cyan
docker build -f composegen/Dockerfile -t gridlock-composegen composegen

# Step 2: Run the generator to create helics_runner.json
Write-Host "Generating helics_runner.json configuration..." -ForegroundColor Cyan
docker run --rm `
  -v "${PSScriptRoot}/config:/config" `
  -v "${PSScriptRoot}/data:/data" `
  gridlock-composegen

# Step 3: Build the runner container (contains all federates + helics_runner)
Write-Host "Building simulation runner container..." -ForegroundColor Cyan
docker build -f runner/Dockerfile -t gridlock-runner runner

# Step 4: Run the simulation using helics_runner
Write-Host "Starting co-simulation with helics_runner..." -ForegroundColor Cyan
docker run --rm `
  -v "${PSScriptRoot}/config:/config" `
  -v "${PSScriptRoot}/data:/data" `
  gridlock-runner

Write-Host "=== Simulation complete ===" -ForegroundColor Green

# Note: For backward compatibility, docker-compose.yml is still generated
# To use the old method, run: docker compose -f config/tmp/docker-compose.yml up --build