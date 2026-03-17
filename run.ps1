# PowerShell equivalent of run.sh
$ErrorActionPreference = "Stop"

# Step 1: Run infdb data setup to resolve grids and write to CST metadata store
Write-Host "=== Step 1: infdb data setup ==="
docker build -f databases/infdb/Dockerfile -t infdb databases/infdb
docker run --rm `
  -v "${PSScriptRoot}/databases/infdb/configs:/workspaces/infdb/configs:ro" `
  -v "${PSScriptRoot}/meta_store:/workspaces/infdb/meta_store" `
  -v "${PSScriptRoot}/config:/config:ro" `
  --env-file config/preflight.env `
  --add-host=host.docker.internal:host-gateway `
  infdb

# Step 2: Build and run composegen (reads manifest.json from meta_store)
Write-Host "=== Step 2: composegen ==="
docker build -f composegen/Dockerfile -t composegen composegen
docker run --rm `
  -v "${PSScriptRoot}/config:/config" `
  -v "${PSScriptRoot}/data:/data" `
  -v "${PSScriptRoot}/meta_store:/app/meta_store" `
  composegen

# Step 3: Launch the experiment with docker compose
Write-Host "=== Step 3: docker compose up ==="
$metaDir = Join-Path $PSScriptRoot 'meta_store'
$latest = Get-ChildItem -Path (Join-Path $metaDir '*.yml') -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $latest) {
	$latest = Get-ChildItem -Path (Join-Path $metaDir '*.yaml') -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
$composeFile = if ($latest) { $latest.FullName } else { Join-Path $metaDir 'docker-compose.yml' }
docker compose -f $composeFile up --build --remove-orphans