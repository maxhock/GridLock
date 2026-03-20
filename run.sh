#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFLIGHT_ENV_FILE="${PREFLIGHT_ENV_FILE:-$SCRIPT_DIR/config/preflight.env}"
PREFLIGHT_COMPOSE_FILE="$SCRIPT_DIR/docker-compose.preflight.yaml"
GENERATED_DIR="$SCRIPT_DIR/generated"
COMPOSE_FILE="$GENERATED_DIR/docker-compose.yaml"

print_stage() {
  echo "=== $1 ==="
}

load_env() {
  if [ -f "$PREFLIGHT_ENV_FILE" ]; then
    set -a
    source "$PREFLIGHT_ENV_FILE"
    set +a
  elif [ -f "$SCRIPT_DIR/cosim.env" ]; then
    set -a
    source "$SCRIPT_DIR/cosim.env"
    set +a
  fi

  export PREFLIGHT_ENV_FILE
  export INFDB_ENV_FILE="${INFDB_ENV_FILE:-$PREFLIGHT_ENV_FILE}"
}

preflight_compose() {
  docker compose --env-file "$PREFLIGHT_ENV_FILE" -f "$PREFLIGHT_COMPOSE_FILE" "$@"
}

cleanup() {
  preflight_compose down --remove-orphans >/dev/null 2>&1 || true
}

main() {
  print_stage "Stage 1: load environment"
  load_env

  trap cleanup EXIT

  print_stage "Stage 2: run preflight compose"
  preflight_compose up -d --wait database mongodb
  preflight_compose up --build --quiet-build --abort-on-container-exit --exit-code-from composegen --no-deps infdb composegen

  print_stage "Stage 3: run experiment compose"
  docker compose -f "$COMPOSE_FILE" up --build --quiet-build --remove-orphans --abort-on-container-exit
}

main "$@"