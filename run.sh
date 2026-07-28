#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

# --- arguments -------------------------------------------------------------
EXPERIMENT=""
CLEANUP_DBS=0
SIM_TIMEOUT="${GRIDLOCK_SIM_TIMEOUT:-0}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      echo "Usage: $0 [OPTIONS] [EXPERIMENT_FILE]"
      echo ""
      echo "Run the GridLock co-simulation experiment."
      echo ""
      echo "Arguments:"
      echo "  EXPERIMENT_FILE    Path to experiment YAML (default: experiment-LV.yml)"
      echo ""
      echo "Options:"
      echo "  --cleanup-dbs      Stop CST databases on exit (default: leave running)"
      echo "  --timeout SECONDS  Abort the simulation if it runs longer (default: no limit,"
      echo "                     override via GRIDLOCK_SIM_TIMEOUT)"
      echo "  --help, -h         Show this help message"
      exit 0
      ;;
    --cleanup-dbs) CLEANUP_DBS=1; shift ;;
    --timeout) SIM_TIMEOUT="${2:?--timeout needs a value in seconds}"; shift 2 ;;
    *)
      if [[ -z "$EXPERIMENT" ]]; then EXPERIMENT="$1"; else echo "Unknown argument: $1" >&2; fi
      shift
      ;;
  esac
done
if [[ -n "$EXPERIMENT" ]]; then
  CONFIG_PATH="/config/$(basename "$EXPERIMENT")"
fi

# --- environment -----------------------------------------------------------
ENV_FILE="${PREFLIGHT_ENV_FILE:-config/preflight.env}"
[[ -f "$ENV_FILE" ]] || { echo "Missing env file: $ENV_FILE" >&2; exit 1; }
set -a; source "$ENV_FILE"; set +a
export INFDB_ENV_FILE="${INFDB_ENV_FILE:-$ENV_FILE}"
export CONFIG_PATH="${CONFIG_PATH:-/config/experiment-LV.yml}"
export GIT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")

DC="docker compose --env-file $ENV_FILE -f docker-compose.preflight.yaml"

# --- helpers ---------------------------------------------------------------
db_running() {
  docker inspect --format '{{.State.Running}}' "$1" 2>/dev/null | grep -q true
}

# --- main ------------------------------------------------------------------
echo "=== Stage 1: CST databases ==="
PROJECT=$($DC config --name 2>/dev/null || echo "gridlock-preflight")
if ! (db_running "${PROJECT}-database-1" && db_running "${PROJECT}-mongodb-1"); then
  echo "Starting CST databases..."
  $DC up -d --wait database mongodb
  echo "CST databases are up."
else
  echo "CST databases already running, skipping startup."
fi

if [[ "$CLEANUP_DBS" == "1" ]]; then
  trap "$DC down database mongodb --remove-orphans" EXIT
fi

echo "=== Stage 2: infdb ==="
$DC up --build --quiet-build --abort-on-container-exit --no-deps infdb

echo "=== Stage 3: composegen ==="
$DC up --build --quiet-build --abort-on-container-exit --exit-code-from composegen --no-deps composegen

echo "=== Stage 4: simulation ==="
SIM_DC="docker compose -f generated/docker-compose.yaml"

# --abort-on-container-failure (not --abort-on-container-exit) tears the whole
# federation down as soon as any federate exits *non-zero*. Federates that
# finish normally still get to flush their final timeseries writes, so this
# keeps the fix for the "logger blocks finish" issue while making a crashed
# federate fail the run instead of leaving its siblings blocked on the broker
# forever.
set +e
if [[ "$SIM_TIMEOUT" -gt 0 ]]; then
  timeout --foreground "${SIM_TIMEOUT}" \
    $SIM_DC up --build --quiet-build --remove-orphans --abort-on-container-failure
  SIM_STATUS=$?
else
  $SIM_DC up --build --quiet-build --remove-orphans --abort-on-container-failure
  SIM_STATUS=$?
fi
set -e

if [[ "$SIM_STATUS" -eq 124 ]]; then
  # `timeout` fired: the federates are still running, so stop them explicitly.
  echo "Simulation exceeded ${SIM_TIMEOUT}s without finishing; tearing down." >&2
  $SIM_DC down --remove-orphans || true
elif [[ "$SIM_STATUS" -ne 0 ]]; then
  echo "Simulation failed (exit $SIM_STATUS)." >&2
fi

exit "$SIM_STATUS"