# PowerShell equivalent of run.sh
$ErrorActionPreference = "Stop"

$PreflightEnvFile = if ($env:PREFLIGHT_ENV_FILE) { $env:PREFLIGHT_ENV_FILE } else { Join-Path $PSScriptRoot "config/preflight.env" }
$PreflightComposeFile = Join-Path $PSScriptRoot "docker-compose.preflight.yaml"
$GeneratedDir = Join-Path $PSScriptRoot "generated"
$ComposeFile = Join-Path $GeneratedDir "docker-compose.yaml"

function Write-Stage {
  param([string]$Message)

  Write-Host "=== $Message ==="
}

function Import-EnvFile {
  if (-not (Test-Path $PreflightEnvFile)) {
    throw "Missing env file: $PreflightEnvFile"
  }

  foreach ($line in Get-Content $PreflightEnvFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
      continue
    }

    $separatorIndex = $trimmed.IndexOf("=")
    if ($separatorIndex -lt 0) {
      continue
    }

    $name = $trimmed.Substring(0, $separatorIndex).Trim()
    $value = $trimmed.Substring($separatorIndex + 1)
    Set-Item -Path "Env:$name" -Value $value
  }

  $env:PREFLIGHT_ENV_FILE = $PreflightEnvFile
  if (-not $env:INFDB_ENV_FILE) {
    $env:INFDB_ENV_FILE = $PreflightEnvFile
  }
}

function Invoke-PreflightCompose {
  param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ComposeArgs
  )

  & docker compose --env-file $PreflightEnvFile -f $PreflightComposeFile @ComposeArgs
  if ($LASTEXITCODE -ne 0) {
    throw "docker compose failed with exit code $LASTEXITCODE"
  }
}

function Cleanup-Preflight {
  try {
    & docker compose --env-file $PreflightEnvFile -f $PreflightComposeFile down --remove-orphans | Out-Null
  }
  catch {
  }
}

Write-Stage "Stage 1: load environment"
Import-EnvFile

try {
  Write-Stage "Stage 2: run preflight compose"
  Invoke-PreflightCompose up -d --wait database mongodb
  Invoke-PreflightCompose up --build --quiet-build --abort-on-container-exit --exit-code-from composegen --no-deps infdb composegen

  Write-Stage "Stage 3: run experiment compose"
  & docker compose -f $ComposeFile up --build --quiet-build --remove-orphans --abort-on-container-exit
  if ($LASTEXITCODE -ne 0) {
    throw "docker compose failed with exit code $LASTEXITCODE"
  }
}
finally {
  Cleanup-Preflight
}