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

# A row count keyed on "whichever TestGrid_* scenario sorts last" would still
# report a pass if *this* run wrote nothing but an earlier local/CI run left
# rows behind (run.sh leaves the DBs up between runs by default) - the exact
# silent-failure mode AGENTS.md's "fail loudly" rule exists to catch. Snapshot
# the newest scenario before the run and require a strictly newer one to
# exist after it, so the row count below is provably about this run.
pg_query() {
  docker run --rm --network host \
    -e PGPASSWORD="$CST_POSTGRES_PASSWORD" \
    postgres:16-alpine \
    psql -h localhost -p "$CST_POSTGRES_PORT" -U "$CST_POSTGRES_USER" -d "$CST_POSTGRES_DB" \
    -tAc "$1" 2>/dev/null
}

LATEST_SCENARIO_SQL="SELECT COALESCE(max(scenario), '')
  FROM \"${ANALYSIS_SCHEMA}\".hdt_double
  WHERE scenario LIKE '${SCENARIO_PREFIX}\_%';"

# Empty on a fresh database (schema doesn't exist yet), not an error we need
# to distinguish - psql's error goes to stderr, so stdout is simply empty.
BEFORE_SCENARIO="$(pg_query "$LATEST_SCENARIO_SQL")"
BEFORE_SCENARIO="${BEFORE_SCENARIO//[[:space:]]/}"

echo "=== Running $EXPERIMENT through run.sh ==="
./run.sh --timeout 600 "$EXPERIMENT"
RUN_STATUS=$?

if [[ "$RUN_STATUS" -ne 0 ]]; then
  echo "run.sh failed (exit $RUN_STATUS); skipping data verification." >&2
  docker compose -f generated/docker-compose.yaml down --remove-orphans >/dev/null 2>&1 || true
  exit "$RUN_STATUS"
fi

echo "=== Verifying the run wrote data to Postgres ==="
AFTER_SCENARIO="$(pg_query "$LATEST_SCENARIO_SQL")"
AFTER_SCENARIO="${AFTER_SCENARIO//[[:space:]]/}"

docker compose -f generated/docker-compose.yaml down --remove-orphans >/dev/null 2>&1 || true

if [[ -z "$AFTER_SCENARIO" || "$AFTER_SCENARIO" == "$BEFORE_SCENARIO" ]]; then
  echo "No new ${SCENARIO_PREFIX}_* scenario appeared in ${ANALYSIS_SCHEMA} after the run" >&2
  echo "(before: '${BEFORE_SCENARIO:-<none>}', after: '${AFTER_SCENARIO:-<none>}')." >&2
  exit 1
fi

ROW_COUNT="$(pg_query "SELECT count(*) FROM \"${ANALYSIS_SCHEMA}\".hdt_double WHERE scenario = '${AFTER_SCENARIO}';")"
QUERY_STATUS=$?
ROW_COUNT="${ROW_COUNT//[[:space:]]/}"

if [[ "$QUERY_STATUS" -ne 0 || -z "$ROW_COUNT" ]]; then
  echo "Could not query Postgres for run results." >&2
  exit 1
fi

echo "Scenario '${AFTER_SCENARIO}' (this run) wrote ${ROW_COUNT} row(s) to ${ANALYSIS_SCHEMA}.hdt_double."

if [[ "$ROW_COUNT" -eq 0 ]]; then
  echo "Run exited 0 but wrote no data - treating as a failure." >&2
  exit 1
fi

echo "e2e test passed."
