# PowerShell equivalent of run.sh
$ErrorActionPreference = "Stop"

# Build the compose generator image
docker build -f composegen/Dockerfile -t composegen composegen

# Run the generator, mounting only the config folder so docker-compose.yaml is written to config/
docker run --rm -v "${PSScriptRoot}/config:/config" -v "${PSScriptRoot}/data:/data"  composegen

# Now launch the experiment with docker compose, pointing at the generated file
# Find newest yaml in meta_store and use it as the compose file (fallback to meta_store/docker-compose.yml)
$metaDir = Join-Path $PSScriptRoot 'meta_store'
$latest = Get-ChildItem -Path (Join-Path $metaDir '*.yml') -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $latest) {
	$latest = Get-ChildItem -Path (Join-Path $metaDir '*.yaml') -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
$composeFile = if ($latest) { $latest.FullName } else { Join-Path $metaDir 'docker-compose.yml' }
docker compose -f $composeFile up --build --remove-orphans