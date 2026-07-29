# PowerShell equivalent of run.sh. Keep the two in step: a Windows user running
# this script must get the same stages, the same abort behaviour and the same
# exit code as a Linux user running run.sh.
$ErrorActionPreference = "Stop"

# PowerShell 5.1 has no $IsWindows and only ever runs on Windows.
$OnWindows = if ($null -eq $IsWindows) { $true } else { $IsWindows }

function Show-Help {
  Write-Host "Usage: run.ps1 [OPTIONS] [EXPERIMENT_FILE]"
  Write-Host ""
  Write-Host "Run the GridLock co-simulation experiment."
  Write-Host ""
  Write-Host "Arguments:"
  Write-Host "  EXPERIMENT_FILE    Path to experiment YAML (default: experiment-LV.yml)"
  Write-Host ""
  Write-Host "Options:"
  Write-Host "  --cleanup-dbs      Stop CST databases on exit (default: leave running)"
  Write-Host "  --timeout SECONDS  Abort the simulation if it runs longer (default: no limit,"
  Write-Host "                     override via GRIDLOCK_SIM_TIMEOUT)"
  Write-Host "  --help, -h         Show this help message"
}

# --- arguments -------------------------------------------------------------
$Experiment = ""
$CleanupDBs = $false
$SimTimeout = if ($env:GRIDLOCK_SIM_TIMEOUT) { [int]$env:GRIDLOCK_SIM_TIMEOUT } else { 0 }

for ($i = 0; $i -lt $args.Count; $i++) {
  switch ($args[$i]) {
    { $_ -in "--help", "-h" } {
      Show-Help
      exit 0
    }
    "--cleanup-dbs" { $CleanupDBs = $true }
    "--timeout" {
      if ($i + 1 -ge $args.Count) {
        Write-Error "--timeout needs a value in seconds"
        exit 1
      }
      $SimTimeout = [int]$args[$i + 1]
      $i++
    }
    default {
      # Same as run.sh: the first bare argument is the experiment, any further
      # one is reported but does not abort the run.
      if (-not $Experiment) { $Experiment = $args[$i] }
      else { Write-Host "Unknown argument: $($args[$i])" }
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

$GitCommit = & git rev-parse HEAD 2>$null
if ($LASTEXITCODE -ne 0 -or -not $GitCommit) { $GitCommit = "unknown" }
$env:GIT_COMMIT = $GitCommit

# Linux-style uid/gid mapping is what keeps generated/ owned by the invoking
# user rather than root. Docker Desktop on Windows already maps ownership for
# bind mounts, so leave these at the compose defaults (0:0) there.
if (-not $OnWindows) {
  $env:HOST_UID = & id -u
  $env:HOST_GID = & id -g
}

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

# Runs from before infdb/composegen were pinned to the invoking user left
# root-owned files in generated/. Those containers can no longer write into
# them, and the resulting failure is a bare "Permission denied" from deep
# inside a metadata writer, so name the problem and the fix instead. Windows
# bind mounts carry no uid, so the check only applies elsewhere.
function Confirm-GeneratedOwnership {
  if ($OnWindows -or -not (Test-Path $GeneratedDir)) { return }

  $uid = & id -u
  $gid = & id -g
  $foreign = & find $GeneratedDir -mindepth 1 ! -user $uid -print -quit 2>$null

  if ($foreign) {
    Write-Host "generated/ still holds files owned by another user (e.g. $foreign),"
    Write-Host "left by earlier runs that executed as root. Reclaim them with:"
    Write-Host "  docker run --rm -v `"`$PWD/generated:/g`" alpine chown -R ${uid}:${gid} /g"
    exit 1
  }
}

# --- main ------------------------------------------------------------------
# Only reached as the script's exit code when stage 4 ran; an earlier stage
# throws instead, and $ErrorActionPreference = "Stop" ends the script there.
$SimStatus = 1

try {
  Confirm-GeneratedOwnership

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

  Write-Host "=== Stage 2: infdb ==="
  Invoke-PreflightCompose up --build --quiet-build --abort-on-container-exit --exit-code-from infdb --no-deps infdb

  Write-Host "=== Stage 3: composegen ==="
  Invoke-PreflightCompose up --build --quiet-build --abort-on-container-exit --exit-code-from composegen --no-deps composegen

  Write-Host "=== Stage 4: simulation ==="
  # --abort-on-container-failure (not --abort-on-container-exit) tears the whole
  # federation down as soon as any federate exits *non-zero*. Federates that
  # finish normally still get to flush their final timeseries writes, so this
  # keeps the fix for the "logger blocks finish" issue while making a crashed
  # federate fail the run instead of leaving its siblings blocked on the broker
  # forever.
  $SimArgs = @(
    "compose", "-f", $ComposeFile,
    "up", "--build", "--quiet-build", "--remove-orphans", "--abort-on-container-failure"
  )

  # PowerShell has no `timeout`, so run compose as a child process and stop it
  # ourselves. 124 mirrors the exit code GNU timeout uses in run.sh.
  $sim = Start-Process -FilePath "docker" -ArgumentList $SimArgs -NoNewWindow -PassThru

  if ($SimTimeout -gt 0) {
    if ($sim.WaitForExit($SimTimeout * 1000)) {
      $SimStatus = $sim.ExitCode
    }
    else {
      $sim.Kill()
      $sim.WaitForExit()
      $SimStatus = 124
    }
  }
  else {
    $sim.WaitForExit()
    $SimStatus = $sim.ExitCode
  }

  if ($SimStatus -eq 124) {
    # The timeout fired: the federates are still running, so stop them explicitly.
    Write-Host "Simulation exceeded ${SimTimeout}s without finishing; tearing down."
    & docker compose -f $ComposeFile down --remove-orphans 2>$null
  }
  elseif ($SimStatus -ne 0) {
    Write-Host "Simulation failed (exit $SimStatus)."
  }
}
finally {
  if ($CleanupDBs) {
    & docker compose --env-file $PreflightEnvFile -f $PreflightComposeFile down database mongodb --remove-orphans 2>$null
  }
}

exit $SimStatus
