# PowerShell equivalent of run.sh
$ErrorActionPreference = "Stop"

# --- arguments -------------------------------------------------------------
$Experiment = ""
$CleanupDBs = $false
foreach ($arg in $args) {
  switch ($arg) {
    "--help" {
      Write-Host "Usage: $PSCommandPath [OPTIONS] [EXPERIMENT_FILE]"
      Write-Host ""
      Write-Host "Run the GridLock co-simulation experiment."
      Write-Host ""
      Write-Host "Arguments:"
      Write-Host "  EXPERIMENT_FILE    Path to experiment YAML (default: experiment-LV.yml)"
      Write-Host ""
      Write-Host "Options:"
      Write-Host "  --cleanup-dbs      Stop CST databases on exit (default: leave running)"
      Write-Host "  --help, -h         Show this help message"
      exit 0
    }
    "-h" { exit 0 } # reuse help above
    "--cleanup-dbs" { $CleanupDBs = $true }
    default {
      if (-not $Experiment) { $Experiment = $arg }
      else { Write-Error "Unknown argument: $arg"; exit 1 }
    }
  }
}

# --- paths -----------------------------------------------------------------
$PreflightEnvFile = if ($env:PREFLIGHT_ENV_FILE) { $env:PREFLIGHT_ENV_FILE } else { Join-Path $PSScriptRoot "config/preflight.env" }
$PreflightComposeFile = Join-Path $PSScriptRoot "docker-compose.preflight.yaml"
$GeneratedDir = Join-Path $PSScriptRoot "generated"
$ComposeFile = Join-Path $GeneratedDir "docker-compose.yaml"

# --- environment -----------------------------------------------------------
if (-not (Test-Path $PreflightEnvFile)) {
  Write-Error "Missing env file: $PreflightEnvFile"
  exit 1
}

foreach ($line in Get-Content $PreflightEnvFile) {
  $trimmed = $line.Trim()
  if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
  $separatorIndex = $trimmed.IndexOf("=")
  if ($separatorIndex -lt 0) { continue }
  $name = $trimmed.Substring(0, $separatorIndex).Trim()
  $value = $trimmed.Substring($separatorIndex + 1)
  Set-Item -Path "Env:$name" -Value $value
}

$env:PREFLIGHT_ENV_FILE = $PreflightEnvFile
$env:INFDB_ENV_FILE = if ($env:INFDB_ENV_FILE) { $env:INFDB_ENV_FILE } else { $PreflightEnvFile }
if ($Experiment) {
  $env:CONFIG_PATH = "/config/" + [System.IO.Path]::GetFileName($Experiment)
}
else {
  $env:CONFIG_PATH = "/config/experiment-LV.yml"
}
$env:GIT_COMMIT = & git rev-parse HEAD 2>$null || "unknown"

# --- helpers ---------------------------------------------------------------
function Invoke-PreflightCompose {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
  & docker compose --env-file $PreflightEnvFile -f $PreflightComposeFile @ComposeArgs
  if ($LASTEXITCODE -ne 0) {
    throw "docker compose failed with exit code $LASTEXITCODE"
  }
}

function Test-DBRunning {
  param([string]$Name)
  $running = & docker inspect --format '{{.State.Running}}' $Name 2>$null
  return $running -eq "true"
}

# --- main ------------------------------------------------------------------
Write-Host "=== Stage 1: CST databases ==="
$Project = & docker compose --env-file $PreflightEnvFile -f $PreflightComposeFile config --name 2>$null
if (-not $Project) { $Project = "gridlock-preflight" }

$PgContainer = "$Project-database-1"
$MongoContainer = "$Project-mongodb-1"

if (-not (Test-DBRunning $PgContainer) -or -not (Test-DBRunning $MongoContainer)) {
  Write-Host "Starting CST databases..."
  Invoke-PreflightCompose up -d --wait database mongodb
  Write-Host "CST databases are up."
}
else {
  Write-Host "CST databases already running, skipping startup."
}

if ($CleanupDBs) {
  try {
    # Main logic below
  }
  finally {
    & docker compose --env-file $PreflightEnvFile -f $PreflightComposeFile down database mongodb --remove-orphans 2>$null
  }
}

Write-Host "=== Stage 2: infdb ==="
Invoke-PreflightCompose up --build --quiet-build --abort-on-container-exit --no-deps infdb

Write-Host "=== Stage 3: composegen ==="
Invoke-PreflightCompose up --build --quiet-build --abort-on-container-exit --exit-code-from composegen --no-deps composegen

Write-Host "=== Stage 4: simulation ==="
# --abort-on-container-failure (not --abort-on-container-exit) tears the whole
# federation down as soon as any federate exits *non-zero*. Federates that
# finish normally still get to flush their final timeseries writes, so this
# keeps the fix for the "logger blocks finish" issue while making a crashed
# federate fail the run instead of leaving its siblings blocked on the broker
# forever.
& docker compose -f $ComposeFile up --build --quiet-build --remove-orphans --abort-on-container-failure
if ($LASTEXITCODE -ne 0) {
  throw "simulation failed with exit code $LASTEXITCODE"
}