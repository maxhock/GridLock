# PowerShell equivalent of run.sh
$ErrorActionPreference = "Stop"

# Build and run data generation
docker build -f data-generation/Dockerfile -t data-generation data-generation
docker run --rm -v "${PSScriptRoot}/config:/config" -v "${PSScriptRoot}/data:/data" data-generation

# Build the compose generator image
docker build -f composegen/Dockerfile -t composegen composegen

# Run the generator, mounting only the config folder so docker-compose.yaml is written to config/
docker run --rm -v "${PSScriptRoot}/config:/config" composegen

# Now launch the experiment with docker compose, pointing at the generated file
docker compose -f config/tmp/docker-compose.yml up --build --remove-orphans