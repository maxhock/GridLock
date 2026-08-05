#!/bin/bash
set -uo pipefail

cd "$(dirname "$0")"

# config/experiment-local-grid.yml needs no external InfDB access (a local
# pandapower layout, not a location query) and only takes 24 simulation
# steps, so it is fast enough to run on every push.
EXPERIMENT="config/experiment-local-grid.yml"
ANALYSIS_SCHEMA="TestGridAnalysis"
SCENARIO_PREFIX="TestGrid"

ENV_FILE="${PREFLIGHT_ENV_FILE:-config/preflight.env}"
[[ -f "$ENV_FILE" ]] || { echo "Missing env file: $ENV_FILE" >&2; exit 1; }
set -a; source "$ENV_FILE"; set +a

echo "=== Running $EXPERIMENT through run.sh ==="
./run.sh --timeout 600 "$EXPERIMENT"
RUN_STATUS=$?

if [[ "$RUN_STATUS" -ne 0 ]]; then
  echo "run.sh failed (exit $RUN_STATUS); skipping data verification." >&2
  docker compose -f generated/docker-compose.yaml down --remove-orphans >/dev/null 2>&1 || true
  exit "$RUN_STATUS"
fi

# A run that exits 0 but wrote nothing is the silent-failure mode this
# project keeps hitting (see AGENTS.md's "fail loudly" rule), so check
# actual rows rather than trusting the exit code alone. Scenario names are
# "<analysis>_<timestamp>", which sorts lexicographically the same as
# chronologically, so the latest one is easy to find without touching Mongo.
echo "=== Verifying the run wrote data to Postgres ==="
ROW_COUNT=$(docker run --rm --network host \
  -e PGPASSWORD="$CST_POSTGRES_PASSWORD" \
  postgres:16-alpine \
  psql -h localhost -p "$CST_POSTGRES_PORT" -U "$CST_POSTGRES_USER" -d "$CST_POSTGRES_DB" \
  -tAc "SELECT count(*) FROM \"${ANALYSIS_SCHEMA}\".hdt_double
        WHERE scenario = (
          SELECT scenario FROM \"${ANALYSIS_SCHEMA}\".hdt_double
          WHERE scenario LIKE '${SCENARIO_PREFIX}\_%'
          ORDER BY scenario DESC LIMIT 1
        );")
QUERY_STATUS=$?

docker compose -f generated/docker-compose.yaml down --remove-orphans >/dev/null 2>&1 || true

if [[ "$QUERY_STATUS" -ne 0 ]]; then
  echo "Could not query Postgres for run results." >&2
  exit 1
fi

ROW_COUNT="${ROW_COUNT//[[:space:]]/}"
echo "Latest ${SCENARIO_PREFIX}_* scenario wrote ${ROW_COUNT} row(s) to ${ANALYSIS_SCHEMA}.hdt_double."

if [[ -z "$ROW_COUNT" || "$ROW_COUNT" -eq 0 ]]; then
  echo "Run exited 0 but wrote no data - treating as a failure." >&2
  exit 1
fi

echo "e2e test passed."
