#!/bin/bash
set -uo pipefail

cd "$(dirname "$0")"

# config/experiment-e2e-kerber-parity.yml runs the real grid + house_player
# federates over HELICS against the existing Kerber Landnetz Freileitung 1
# fixture (data/input/kerber_landnetz_freileitung_1.xlsx, already used by
# experiment-local-grid.yml). tools/verify_pandapower_parity.py then checks
# GridLock's own published voltages against an independent pure-pandapower
# replica of the same network and load timeseries - catching wiring/
# unit-conversion bugs that "the containers didn't crash" cannot.
EXPERIMENT="config/experiment-e2e-kerber-parity.yml"
SCENARIOS_DIR="generated/scenarios"

# Prefer the repo's own .venv (built from tools/requirements.txt) over
# whatever "python3" resolves to on PATH, so this works out of the box once
# that venv exists instead of requiring a separate one just for this script.
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x .venv/bin/python3 ]]; then
    PYTHON_BIN=".venv/bin/python3"
  else
    PYTHON_BIN="python3"
  fi
fi

ENV_FILE="${PREFLIGHT_ENV_FILE:-config/preflight.env}"
[[ -f "$ENV_FILE" ]] || { echo "Missing env file: $ENV_FILE" >&2; exit 1; }
set -a; source "$ENV_FILE"; set +a

# Guessing "the newest scenario file" is stale-prone the same way e2e-test.sh's
# row-count check was (see that commit): two runs of this experiment can both
# leave a KerberParity_* file behind. Snapshot the directory before the run
# and diff it after, so the scenario handed to the verifier is provably the
# one this run just wrote, not an older leftover.
mkdir -p "$SCENARIOS_DIR"
BEFORE_LIST="$(mktemp)"
AFTER_LIST="$(mktemp)"
trap 'rm -f "$BEFORE_LIST" "$AFTER_LIST"' EXIT
ls "$SCENARIOS_DIR" 2>/dev/null | sort > "$BEFORE_LIST"

echo "=== Running $EXPERIMENT through run.sh ==="
./run.sh --timeout 300 "$EXPERIMENT"
RUN_STATUS=$?

if [[ "$RUN_STATUS" -ne 0 ]]; then
  echo "run.sh failed (exit $RUN_STATUS); skipping pandapower parity check." >&2
  docker compose -f generated/docker-compose.yaml down --remove-orphans >/dev/null 2>&1 || true
  exit "$RUN_STATUS"
fi

ls "$SCENARIOS_DIR" 2>/dev/null | sort > "$AFTER_LIST"
NEW_FILES="$(comm -13 "$BEFORE_LIST" "$AFTER_LIST")"
NEW_COUNT="$(printf '%s\n' "$NEW_FILES" | grep -c . || true)"

if [[ "$NEW_COUNT" -ne 1 ]]; then
  echo "Expected exactly one new scenario file in $SCENARIOS_DIR after the run, found $NEW_COUNT: $NEW_FILES" >&2
  docker compose -f generated/docker-compose.yaml down --remove-orphans >/dev/null 2>&1 || true
  exit 1
fi

SCENARIO="${NEW_FILES%.json}"

echo "=== Comparing against an independent pandapower replica ==="
"$PYTHON_BIN" tools/verify_pandapower_parity.py \
  --experiment "$EXPERIMENT" \
  --scenario "$SCENARIO" \
  --pg-host localhost \
  --pg-port "$CST_POSTGRES_PORT" \
  --pg-db "$CST_POSTGRES_DB" \
  --pg-user "$CST_POSTGRES_USER" \
  --pg-password "$CST_POSTGRES_PASSWORD"
VERIFY_STATUS=$?

docker compose -f generated/docker-compose.yaml down --remove-orphans >/dev/null 2>&1 || true

exit "$VERIFY_STATUS"
