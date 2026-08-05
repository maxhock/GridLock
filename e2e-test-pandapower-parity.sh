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
PYTHON_BIN="${PYTHON_BIN:-python3}"

ENV_FILE="${PREFLIGHT_ENV_FILE:-config/preflight.env}"
[[ -f "$ENV_FILE" ]] || { echo "Missing env file: $ENV_FILE" >&2; exit 1; }
set -a; source "$ENV_FILE"; set +a

echo "=== Running $EXPERIMENT through run.sh ==="
./run.sh --timeout 300 "$EXPERIMENT"
RUN_STATUS=$?

if [[ "$RUN_STATUS" -ne 0 ]]; then
  echo "run.sh failed (exit $RUN_STATUS); skipping pandapower parity check." >&2
  docker compose -f generated/docker-compose.yaml down --remove-orphans >/dev/null 2>&1 || true
  exit "$RUN_STATUS"
fi

echo "=== Comparing against an independent pandapower replica ==="
"$PYTHON_BIN" tools/verify_pandapower_parity.py \
  --experiment "$EXPERIMENT" \
  --pg-host localhost \
  --pg-port "$CST_POSTGRES_PORT" \
  --pg-db "$CST_POSTGRES_DB" \
  --pg-user "$CST_POSTGRES_USER" \
  --pg-password "$CST_POSTGRES_PASSWORD"
VERIFY_STATUS=$?

docker compose -f generated/docker-compose.yaml down --remove-orphans >/dev/null 2>&1 || true

exit "$VERIFY_STATUS"
